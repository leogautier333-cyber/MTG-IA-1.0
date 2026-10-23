from services.phase_one_monitor import analyze_stock_prices, run_forever


def run_once():
    return analyze_stock_prices()


def main():
    run_forever()


if __name__ == "__main__":
    main()