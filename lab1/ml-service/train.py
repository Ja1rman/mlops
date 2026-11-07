from pathlib import Path

import joblib
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier


def main() -> None:
    iris = load_iris()
    model = RandomForestClassifier(n_estimators=50, random_state=42)
    model.fit(iris.data, iris.target)
    joblib.dump(model, Path(__file__).with_name("model.joblib"))


if __name__ == "__main__":
    main()

