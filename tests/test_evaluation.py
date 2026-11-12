def precision_recall_f1(predicted: set[str], expected: set[str]):
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}

def test_evaluation_metrics():
    result = precision_recall_f1({"a","b"}, {"a","c"})
    assert result == {"tp":1,"fp":1,"fn":1,"precision":.5,"recall":.5,"f1":.5}
