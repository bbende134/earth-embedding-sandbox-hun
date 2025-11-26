import os
import sys

sys.path.insert(0, os.path.dirname(__file__) + "/../..")

from fastapi.testclient import TestClient
from pytest import fixture

from api.app import app


@fixture()
def client():
    return TestClient(app)
