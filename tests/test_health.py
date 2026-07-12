from squirrel_health import HealthStore


def test_health_store_is_bounded_and_filters_by_time():
    store = HealthStore(max_samples=2)
    store.add({'t': 1, 'status': 'old'})
    store.add({'t': 2, 'status': 'current'})
    store.add({'t': 3, 'status': 'new'})

    assert store.since(2) == [
        {'t': 2, 'status': 'current'},
        {'t': 3, 'status': 'new'},
    ]


def test_health_store_returns_copies():
    store = HealthStore()
    sample = {'t': 1, 'nested': {'value': 2}}
    store.add(sample)
    sample['status'] = 'changed'

    result = store.snapshot()
    assert result == [{'t': 1, 'nested': {'value': 2}}]
