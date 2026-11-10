"""Lê uma exportação de conversa do WhatsApp e gera um CSV de pedidos.

Uso:
    export ANTHROPIC_API_KEY=...            # sua chave (não vai para o git)
    python3 tools/whatsapp_pedidos.py conversa.txt --out saida/pedidos.csv

A conversa deve ser exportada em "Mais > Exportar conversa > Sem mídia".

Como funciona:
  1. Lê cada mensagem do arquivo (inclui as linhas de continuação de mensagens longas).
  2. Manda as mensagens em lotes para o Claude, que decide se cada uma é pedido
     e extrai os campos (quantidade, destino, observação).
  3. Grava o CSV (separador ";", pronto para o Excel e o Google Sheets).
  4. Guarda o que já foi processado em <out>.vistas.json, então rodar de novo
     com a exportação atualizada só processa as mensagens novas.

Os arquivos de conversa e a saída podem conter dados pessoais: mantenha-os
fora do repositório (a pasta saida/ já está no .gitignore).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-5-5"   # padrão da skill; use --modelo claude-haiku-5-5 para custar menos
BATCH_SIZE = 25

# Formato Android: "05/11/2026 12:30 - Nome: texto"
# Formato iPhone:  "[05/11/2026, 12:30:15] Nome: texto"
LINE_RE = re.compile(
    r"^‎?(?:\[(\d{2}/\d{2}/\d{4}), (\d{1,2}:\d{2})(?::\d{2})?\] |(\d{2}/\d{2}/\d{4}) (\d{1,2}:\d{2}) - )"
    r"(?P<resto>.*)$"
)


@dataclass
class Mensagem:
    id: str          # hash estável: data+hora+autor+texto
    data: str        # AAAA-MM-DD
    hora: str        # HH:MM
    autor: str
    texto: str


class ItemExtraido(BaseModel):
    indice: int = Field(description="Número da mensagem no lote (começa em 0), igual ao informado")
    eh_pedido: bool = Field(description="True se a mensagem pede entrega/refeição; False para conversa, confirmações, fotos etc.")
    quantidade: int | None = Field(None, description="Quantidade de refeições/marmitas pedidas, se informada")
    destino: str | None = Field(None, description="Local ou pessoa para quem é a entrega, se informado")
    observacao: str | None = Field(None, description="Detalhes do pedido (ex.: sem salada, tipo de refeição)")
    precisa_revisao: bool = Field(description="True se a mensagem é ambígua e vale conferir com a pessoa")


class LoteExtraido(BaseModel):
    itens: list[ItemExtraido]


INSTRUCOES = """Você organiza pedidos de alimentação que chegam num grupo de WhatsApp.
Para cada mensagem numerada, responda se é um pedido e extraia os campos.
- Pedido: alguém pede entrega ou quantidade de refeições para ser entregue. Conversa, bom dia,
  confirmações sem quantidade e fotos NÃO são pedidos.
- Use os números exatamente como estão em "indice".
- Não invente quantidades: se não estiver escrito, deixe quantidade vazia e marque precisa_revisao.
- Se a mensagem for ambígua, marque precisa_revisao como true."""


def ler_conversa(caminho: Path) -> list[Mensagem]:
    """Lê o arquivo exportado, juntando as linhas de continuação às mensagens."""
    brutas: list[dict] = []
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE_RE.match(linha)
        if m:
            if m.group(1):      # formato iPhone
                data_txt, hora = m.group(1), m.group(2)
            else:               # formato Android
                data_txt, hora = m.group(3), m.group(4)
            resto = m.group("resto")
            if ": " not in resto:
                # mensagem do sistema ("criou o grupo", "saiu" etc.)
                brutas.append(None)
                continue
            autor, texto = resto.split(": ", 1)
            data = datetime.strptime(data_txt, "%d/%m/%Y").strftime("%Y-%m-%d")
            brutas.append({"data": data, "hora": hora, "autor": autor.strip(), "texto": texto})
        elif brutas and brutas[-1] is not None:
            brutas[-1]["texto"] += "\n" + linha   # continuação de mensagem longa

    mensagens = []
    for b in brutas:
        if not b:
            continue
        chave = f'{b["data"]}|{b["hora"]}|{b["autor"]}|{b["texto"]}'
        mensagens.append(Mensagem(
            id=hashlib.sha1(chave.encode("utf-8")).hexdigest()[:16],
            data=b["data"], hora=b["hora"], autor=b["autor"], texto=b["texto"].strip(),
        ))
    return mensagens


def extrair_lote(client, modelo: str, lote: list[Mensagem]) -> dict[int, ItemExtraido]:
    """Manda um lote ao Claude e devolve os resultados indexados pela posição no lote."""
    # uma mensagem por linha: quebras de linha dentro do texto viram " / "
    linhas = "\n".join(f'[{i}] ({m.data} {m.hora}) {m.autor}: {m.texto.replace(chr(10), " / ")}' for i, m in enumerate(lote))
    resposta = client.messages.parse(
        model=modelo,
        max_tokens=8000,
        system=INSTRUCOES,
        messages=[{"role": "user", "content": f"Mensagens:\n{linhas}"}],
        output_format=LoteExtraido,
    )
    resultado = resposta.parsed_output
    if resultado is None:
        raise RuntimeError("O modelo não devolveu dados estruturados para este lote.")
    return {it.indice: it for it in resultado.itens}


def carregar_vistas(caminho: Path) -> dict:
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("conversa", type=Path, help="arquivo .txt exportado do WhatsApp")
    ap.add_argument("--out", type=Path, default=Path("saida/pedidos.csv"), help="CSV de saída")
    ap.add_argument("--modelo", default=DEFAULT_MODEL, help=f"modelo da Claude (padrão: {DEFAULT_MODEL})")
    ap.add_argument("--desde", help="só mensagens a partir desta data (AAAA-MM-DD)")
    ap.add_argument("--autores", nargs="*", help="só mensagens destes autores (nomes como aparecem no grupo)")
    ap.add_argument("--reprocessar", action="store_true", help="ignora o histórico e processa tudo de novo")
    args = ap.parse_args(argv)

    if not args.conversa.exists():
        print(f"Arquivo não encontrado: {args.conversa}", file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("Defina a variável ANTHROPIC_API_KEY com a sua chave da API.", file=sys.stderr)
        return 1

    import anthropic   # import tardio: a leitura do arquivo funciona sem o SDK instalado
    client = anthropic.Anthropic()

    mensagens = ler_conversa(args.conversa)
    if args.desde:
        mensagens = [m for m in mensagens if m.data >= args.desde]
    if args.autores:
        nomes = {a.lower() for a in args.autores}
        mensagens = [m for m in mensagens if m.autor.lower() in nomes]

    vistas_path = args.out.with_suffix(".vistas.json")
    vistas = {} if args.reprocessar else carregar_vistas(vistas_path)
    novas = [m for m in mensagens if m.id not in vistas]
    print(f"{len(mensagens)} mensagens no filtro, {len(novas)} novas para processar.")

    linhas = []
    for inicio in range(0, len(novas), BATCH_SIZE):
        lote = novas[inicio:inicio + BATCH_SIZE]
        extraidos = extrair_lote(client, args.modelo, lote)
        for i, m in enumerate(lote):
            item = extraidos.get(i)
            if item is None:
                # o modelo não devolveu esta posição: marca para revisão em vez de descartar
                item = ItemExtraido(indice=i, eh_pedido=True, precisa_revisao=True)
            vistas[m.id] = {"processada_em": datetime.now().isoformat(timespec="seconds")}
            if item.eh_pedido:
                linhas.append({
                    "data": m.data, "hora": m.hora, "quem_enviou": m.autor,
                    "quantidade": "" if item.quantidade is None else item.quantidade,
                    "destino": item.destino or "", "observacao": item.observacao or "",
                    "revisar": "sim" if item.precisa_revisao else "",
                    "mensagem_original": m.texto.replace("\n", " / "),
                })
        print(f"  lote {inicio // BATCH_SIZE + 1}: {len(lote)} mensagens")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    arquivo_existe = args.out.exists() and not args.reprocessar
    colunas = ["data", "hora", "quem_enviou", "quantidade", "destino", "observacao", "revisar", "mensagem_original"]
    with args.out.open("a" if arquivo_existe else "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=colunas, delimiter=";")
        if not arquivo_existe:
            w.writeheader()
        w.writerows(linhas)
    vistas_path.write_text(json.dumps(vistas, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(linhas)} pedidos adicionados em {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
