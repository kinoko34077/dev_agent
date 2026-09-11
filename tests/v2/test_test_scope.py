from scripts.test_scope import affected_tests


def test_affected_test_map_returns_small_resource_cluster():
    tests = affected_tests(["src/dev_agent/resources/router.py"])

    assert "tests/v2/test_resource_control.py" in tests
    assert "tests/v2/test_resource_observations.py" in tests
    assert "tests/v2/test_operation.py" not in tests
