"""
Stratégie Grid Trading — Profite de la volatilité en marché neutre.

Place des ordres d'achat/vente à intervalles réguliers autour du prix.
Quand le prix oscille, le bot achète bas et vend haut automatiquement.
Fonctionne même en marché latéral (sideways).
"""

from logger import get_logger

log = get_logger("grid")


class GridStrategy:
    def __init__(self, grid_levels=10, grid_spacing_pct=0.5, position_per_grid=None):
        """
        Args:
            grid_levels: nombre de niveaux au-dessus ET en-dessous du prix
            grid_spacing_pct: % d'espacement entre chaque niveau
            position_per_grid: montant par niveau (calculé auto si None)
        """
        self.grid_levels = grid_levels
        self.grid_spacing_pct = grid_spacing_pct / 100
        self.position_per_grid = position_per_grid
        self.grids = []
        self.initialized = False

    def setup_grid(self, center_price, total_capital):
        """Initialise la grille autour du prix actuel."""
        self.grids = []
        per_grid = total_capital / (self.grid_levels * 2) if not self.position_per_grid else self.position_per_grid

        for i in range(-self.grid_levels, self.grid_levels + 1):
            if i == 0:
                continue
            price_level = center_price * (1 + i * self.grid_spacing_pct)
            self.grids.append({
                "price": round(price_level, 2),
                "side": "buy" if i < 0 else "sell",
                "filled": False,
                "amount": per_grid / price_level,
                "level": i,
            })

        self.grids.sort(key=lambda g: g["price"])
        self.initialized = True
        log.info(f"Grille initialisée: {len(self.grids)} niveaux autour de {center_price:.2f}")
        log.info(f"Range: {self.grids[0]['price']:.2f} — {self.grids[-1]['price']:.2f}")
        return self.grids

    def evaluate(self, current_price):
        """
        Vérifie si le prix a croisé un niveau de la grille.
        Retourne les actions à effectuer.
        """
        if not self.initialized:
            return []

        actions = []
        for grid in self.grids:
            if grid["filled"]:
                continue

            if grid["side"] == "buy" and current_price <= grid["price"]:
                actions.append({
                    "action": "BUY",
                    "price": grid["price"],
                    "amount": grid["amount"],
                    "level": grid["level"],
                })
                grid["filled"] = True
                # Activer le niveau de vente correspondant
                sell_level = -grid["level"]
                for g in self.grids:
                    if g["level"] == sell_level:
                        g["filled"] = False

            elif grid["side"] == "sell" and current_price >= grid["price"]:
                actions.append({
                    "action": "SELL",
                    "price": grid["price"],
                    "amount": grid["amount"],
                    "level": grid["level"],
                })
                grid["filled"] = True
                # Activer le niveau d'achat correspondant
                buy_level = -grid["level"]
                for g in self.grids:
                    if g["level"] == buy_level:
                        g["filled"] = False

        return actions

    def get_grid_status(self):
        """Retourne l'état de la grille."""
        filled_buys = sum(1 for g in self.grids if g["side"] == "buy" and g["filled"])
        filled_sells = sum(1 for g in self.grids if g["side"] == "sell" and g["filled"])
        return {
            "total_levels": len(self.grids),
            "filled_buys": filled_buys,
            "filled_sells": filled_sells,
            "active_buys": self.grid_levels - filled_buys,
            "active_sells": self.grid_levels - filled_sells,
        }
