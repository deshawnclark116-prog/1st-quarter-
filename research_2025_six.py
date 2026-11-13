"""Production-matched six-game lookback against separate 2025 season."""
from datetime import date
from research_opportunity_2025 import walkforward

if __name__=="__main__":
    walkforward(date(2025,10,20),date(2025,11,12),max_games=180,
                lookback=6,output_stem="opportunity-v3-2025-six")
