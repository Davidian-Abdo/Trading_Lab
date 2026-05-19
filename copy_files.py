import os
def assemble_files(file_paths, output_file):
    """
    Reads content from each file in file_paths and writes them sequentially
    into output_file with headers indicating the source file.
    
    :param file_paths: List of file paths to copy
    :param output_file: Path to the assembled output file
    """
    with open(output_file, 'w', encoding='utf-8') as out_f:
        for path in file_paths:
            if not os.path.exists(path):
                print(f"⚠️ File not found: {path}")
                continue

            out_f.write(f"\n\n# ====== Begin of {path} ======\n\n")
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                out_f.write(content)
            out_f.write(f"\n\n# ====== End of {path} ======\n\n")
    
    print(f"✅ All files assembled into {output_file}")


if __name__ == "__main__":
    # Example usage:
    files_to_copy = [
  "README.md",

  "core/__init__.py",
  "core/config.py",
  "core/feed.py",
  "core/behavior.py",
  "core/paper.py",
  "core/strategy.py",
  "core/matrix.py",
  "core/results.py",
  "core/worker.py",
  "core/alerts.py",
  "core/feeds/__init__.py",
  "core/feeds/crypto_feed.py",
  "core/feeds/forex_feed.py",
  "core/feeds/stocks_feed.py",
  "core/feeds/synthetic_feed.py",
  "strategies/breakout.py",
  "strategies/momentum.py",
  "strategies/rsi_meanrev.py",
  "strategies/sma_crossover.py",
  "bots/worker.py",
  "dashboard.py",
  "selftest.py",
  "Dockerfile",
  "docker-compose.yml",
  ".env.shared.example",
  "requirements.txt",
    ]
    assemble_files(files_to_copy, "assembled_code1.txt")
