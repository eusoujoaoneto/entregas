"""Gera financas/index.html a partir de index.html.

O app de Finanças usa o mesmo código do app de Transporte: a página
descobre pelo endereço (/financas/) qual app mostrar. Este script só
ajusta o cabeçalho (título, prévia, ícones) e os caminhos dos arquivos.

Rode depois de mudar index.html:  python3 tools/gerar-financas.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


def rep(s, a, b, count=None):
    n = s.count(a)
    if count is not None and n != count:
        raise SystemExit(f"esperava {count}x, achei {n}x: {a[:70]}")
    if n == 0:
        raise SystemExit(f"não encontrei: {a[:70]}")
    return s.replace(a, b)


s = src
s = rep(s, "https://eusoujoaoneto.github.io/entregas/", "https://eusoujoaoneto.github.io/entregas/financas/")
s = rep(s, "SPLOG · Gestão de entregas", "SPLOG · Finanças da família")
s = rep(s, "Entregas, custos, combustível e lucro da operação em um só lugar.", "Orçamento, lançamentos, metas e previsão do saldo da família.")
s = rep(s, '<meta name="apple-mobile-web-app-title" content="SPLOG">', '<meta name="apple-mobile-web-app-title" content="Finanças">', 1)
s = rep(s, '<meta name="application-name" content="SPLOG">', '<meta name="application-name" content="SPLOG Finanças">', 1)
s = rep(s, '<link rel="icon" href="favicon.svg"', '<link rel="icon" href="../favicon.svg"', 1)
s = rep(s, '<link rel="icon" href="favicon-32.png"', '<link rel="icon" href="../favicon-32.png"', 1)
s = rep(s, '<script src="config.js"></script>', '<script src="../config.js"></script>', 1)
s = rep(s, 'navigator.serviceWorker.register("./sw.js")', 'navigator.serviceWorker.register("../sw.js")', 1)
s = rep(s, "<head>\n", "<head>\n<!-- Gerado a partir de ../index.html por tools/gerar-financas.py. Não edite aqui. -->\n", 1)

os.makedirs(os.path.join(ROOT, "financas"), exist_ok=True)
open(os.path.join(ROOT, "financas", "index.html"), "w", encoding="utf-8").write(s)
print("financas/index.html gerado")
