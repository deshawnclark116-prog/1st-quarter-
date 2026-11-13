"""Equal-horizon independent research: production six-game NBA baseline vs shot family."""
from datetime import date
from research_opportunity import walkforward

if __name__ == "__main__":
    walkforward(date(2026,10,20),date(2026,11,12),max_games=180,
                lookback=6,output_stem="opportunity-v3-six")
