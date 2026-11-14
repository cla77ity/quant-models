"""
qm.universe — списки рынков, общие для нескольких моделей.

    FX       9 валют против доллара + золото (фьючерс GC=F) — модели 08, 09, 10
    INDEX    фьючерсы NQ и ES — модель 09
    FUNDS    индексы, облигации, металлы, нефть как CFD (через ETF с дивидендами) — модели 10, 11
"""
from .portfolio import Asset

# cost — спред + комиссия в б.п.; rate — ставка страны в FRED (для керри); inv — котировка USD/XXX
FX = [
    Asset("EUR", "EURUSD=X", 1.0, rate="IR3TIB01EZM156N", cls="fx"),
    Asset("GBP", "GBPUSD=X", 1.5, rate="IR3TIB01GBM156N", cls="fx"),
    Asset("AUD", "AUDUSD=X", 1.5, rate="IR3TIB01AUM156N", cls="fx"),
    Asset("NZD", "NZDUSD=X", 2.5, rate="IR3TIB01NZM156N", cls="fx"),
    Asset("JPY", "JPY=X", 1.2, inv=True, rate="IR3TIB01JPM156N", cls="fx"),
    Asset("CAD", "CAD=X", 1.5, inv=True, rate="IR3TIB01CAM156N", cls="fx"),
    Asset("CHF", "CHF=X", 2.0, inv=True, rate="IR3TIB01CHM156N", cls="fx"),
    Asset("NOK", "NOK=X", 5.0, inv=True, rate="IR3TIB01NOM156N", cls="fx"),
    Asset("SEK", "SEK=X", 5.0, inv=True, rate="IR3TIB01SEM156N", cls="fx"),
    Asset("XAU", "GC=F", 3.0, cls="metal"),       # фьючерс: стоимость денег уже внутри цены, отдельного керри нет
]

INDEX = [Asset("NQ", "NQ=F", 1.0, markup=0.025, max_move=0.25, cls="eq"),
         Asset("ES", "ES=F", 1.0, markup=0.025, max_move=0.25, cls="eq")]


def fund(key, sym, cost, cls, markup=0.025):
    """CFD на индекс / металл / облигации через ETF с полной доходностью: лонг платит ставку USD + наценку брокера."""
    return Asset(key, sym, cost, fund=True, markup=markup, rp=True, cls=cls, max_move=0.3)


FUNDS = [fund("US500", "SPY", 1, "акции"), fund("US100", "QQQ", 1, "акции"), fund("US30", "DIA", 1.5, "акции"),
         fund("US2000", "IWM", 1.5, "акции"), fund("GER40", "EWG", 1.5, "акции"), fund("JP225", "EWJ", 1.5, "акции"),
         fund("UK100", "EWU", 1.5, "акции"), fund("UST10", "IEF", 2, "облигации"), fund("UST30", "TLT", 2, "облигации"),
         fund("XAU", "GLD", 3, "металлы"), fund("XAG", "SLV", 5, "металлы"), fund("OIL", "USO", 4, "энергия")]
