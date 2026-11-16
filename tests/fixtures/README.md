`synth.py` builds a deterministic synthetic market (no real data). `features_expected.csv` holds the 37 M4 inputs for its
last 6 weeks as computed by the ORIGINAL research code (`lgb_paper/engine_paper.Data` + `features_paper.build`, with the
same synthetic inputs). `tests/test_features_parity.py` checks the repo's `t1.features` reproduces it.
