

def test_worker_words_reads_transit_share():
    import pandas as pd
    from tools.shortage_report import worker_words
    assert worker_words(pd.DataFrame({"a": [1]})) == "자동차"
    assert worker_words(pd.DataFrame({"transit_share": [0.0, 0.0]})) == "자동차"
    assert "모두 대중교통" in worker_words(pd.DataFrame({"transit_share": [1.0]}))
    assert "50%" in worker_words(pd.DataFrame({"transit_share": [0.5]}))
