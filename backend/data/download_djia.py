"""
Tải lại dữ liệu DJIA từ Yahoo Finance -> djia_companies.csv + djia_prices.csv (cùng thư mục).

Chạy trong Docker (không cần cài gì trên máy), từ thư mục gốc repo:
    docker compose run --rm -v ./backend/data:/app/data backend sh -c "pip install -q yfinance && python data/download_djia.py"
    docker compose up -d --build backend   # CSV nằm trong image nên phải build lại mới nạp vào Postgres
"""
import csv
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
START_DATE = "2023-01-01"

# Thành phần DJIA hiện tại (sau đợt thay đổi 11/2024: NVDA thay INTC, SHW thay DOW; 02/2024: AMZN thay WBA)
TICKERS = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
]


def company_row(ticker: str) -> dict:
    info = yf.Ticker(ticker).info
    return {
        "symbol": ticker,
        "name": info.get("shortName", ""),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "country": info.get("country", ""),
        "website": info.get("website", ""),
        "market_cap": info.get("marketCap", 0),
        "pe_ratio": info.get("trailingPE", 0),
        "dividend_yield": info.get("dividendYield") or 0,  # Yahoo đã trả về đơn vị %
        "52_week_high": info.get("fiftyTwoWeekHigh", 0),
        "52_week_low": info.get("fiftyTwoWeekLow", 0),
        "description": info.get("longBusinessSummary", ""),
    }


def price_frame(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(start=START_DATE, interval="1d").reset_index()
    df["Ticker"] = ticker
    return df[["Date", "Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits", "Ticker"]]


def main():
    companies, prices = [], []
    for i, ticker in enumerate(TICKERS, 1):
        print(f"[{i}/{len(TICKERS)}] {ticker}")
        companies.append(company_row(ticker))
        prices.append(price_frame(ticker))
        time.sleep(1)  # tránh rate limit của Yahoo

    all_prices = pd.concat(prices)
    assert all_prices["Ticker"].nunique() == len(TICKERS), "Thiếu dữ liệu giá của một số mã"
    assert all(c["name"] for c in companies), "Thiếu thông tin công ty"

    pd.DataFrame(companies).to_csv(HERE / "djia_companies.csv", index=False, quoting=csv.QUOTE_NONNUMERIC)
    all_prices.to_csv(HERE / "djia_prices.csv", index=False)
    print(f"OK: {len(companies)} công ty, {len(all_prices):,} dòng giá, "
          f"{all_prices['Date'].min()} -> {all_prices['Date'].max()}")


if __name__ == "__main__":
    main()
