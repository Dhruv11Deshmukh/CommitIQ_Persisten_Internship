from app.processing.pipeline import run_ingestion

if __name__ == "__main__":
    result = run_ingestion()
    print(f"Created {result['created'].__len__()} commitments, auto-verified {result['auto_verified'].__len__()}.")                                                                                                                                                     