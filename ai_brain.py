import pandas as pd
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import os

MODEL_FILE = "ai_model.pkl"

class AIBrain:
    def __init__(self):
        self.model = None
        if os.path.exists(MODEL_FILE):
            self.model = joblib.load(MODEL_FILE)
            print("✅ AI Model loaded from file.")
        else:
            print("⚠️ No existing model found, will train a new one.")

    def train_model(self, data: pd.DataFrame):
        """
        Train model based on provided historical data.
        Data must have features in X and binary outcome in y (0 = sell, 1 = buy)
        """
        if data.empty:
            print("⚠️ No data provided for training.")
            return

        X = data.drop("target", axis=1)
        y = data["target"]

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        self.model = RandomForestClassifier(n_estimators=100, random_state=42)
        self.model.fit(X_train, y_train)

        preds = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, preds)
        print(f"📊 Training completed. Accuracy: {accuracy:.2f}")

        joblib.dump(self.model, MODEL_FILE)
        print("💾 Model saved.")

    def predict(self, features: pd.DataFrame):
        """
        Predict buy/sell decision based on features.
        Returns: 1 = buy, 0 = sell
        """
        if self.model is None:
            print("⚠️ No trained model found. Train model first.")
            return None

        prediction = self.model.predict(features)
        return prediction[0]

# Example usage:
if __name__ == "__main__":
    brain = AIBrain()

    # Simulated training example
    dummy_data = pd.DataFrame({
        "feature1": np.random.rand(100),
        "feature2": np.random.rand(100),
        "target": np.random.randint(0, 2, 100)
    })

    brain.train_model(dummy_data)

    # Simulated prediction
    test_features = pd.DataFrame({
        "feature1": [0.45],
        "feature2": [0.67]
    })
    print("Predicted Action:", brain.predict(test_features))
