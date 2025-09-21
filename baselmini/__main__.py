from .cli import main  # Import the CLI entrypoint

# Allow running the package as a module:
#   python -m baselmini run --asof 2024-12-31 --exposures ... --capital ... --liquidity ... --config ... --out ...
if __name__ == "__main__":
    main()  # Delegate straight to CLI main()

