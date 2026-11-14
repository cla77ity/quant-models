"""
qm — quant models: общий код для всех моделей на Python.

    qm.data       загрузка цен (Yahoo) и ставок (FRED) с кэшем в data-cache/
    qm.risk       метрики доходности и риска: волатильность, Sharpe, просадка, VaR, CVaR, Монте-Карло
    qm.portfolio  движок портфеля: сигналы (тренд, керри, risk parity, SMA), размер по волатильности, издержки

Как читать: начни с risk.py — там каждая функция маленькая и объяснена. Потом portfolio.py.
"""
import sys

# консоль Windows по умолчанию не в UTF-8 и падает на русском тексте — переключаем вывод
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
