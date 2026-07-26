from pathlib import Path

path = Path('scripts/update_clientid_periods.py')
text = path.read_text(encoding='utf-8')

text = text.replace(
'''        "fastAnyGoal15Visits": 0,
        "fastAnyGoal30Visits": 0,''',
'''        "fastAnyGoal3Visits": 0,
        "fastAnyGoal15Visits": 0,
        "fastAnyGoal30Visits": 0,'''
)

text = text.replace(
'''        "fastAnyGoal15Visits": int(minimum_any is not None and minimum_any <= 15),
        "fastAnyGoal30Visits": int(minimum_any is not None and 15 < minimum_any <= 30),''',
'''        "fastAnyGoal3Visits": int(minimum_any is not None and 0 <= minimum_any <= 3),
        "fastAnyGoal15Visits": int(minimum_any is not None and 3 < minimum_any <= 15),
        "fastAnyGoal30Visits": int(minimum_any is not None and 15 < minimum_any <= 30),'''
)

text = text.replace(
'''    behavior_names = (
        "fastAnyGoal15Visits", "fastAnyGoal30Visits",''',
'''    behavior_names = (
        "fastAnyGoal3Visits", "fastAnyGoal15Visits", "fastAnyGoal30Visits",'''
)

text = text.replace(
'''    assert result["fastAnyGoal15Visits"]''',
'''    assert result["fastAnyGoal3Visits"] >= 0
    assert result["fastAnyGoal15Visits"]'''
)

required = (
    '"fastAnyGoal3Visits": 0',
    '"fastAnyGoal3Visits": int(minimum_any is not None and 0 <= minimum_any <= 3)',
    '"fastAnyGoal15Visits": int(minimum_any is not None and 3 < minimum_any <= 15)',
    '"fastAnyGoal30Visits": int(minimum_any is not None and 15 < minimum_any <= 30)',
)
for marker in required:
    if marker not in text:
        raise RuntimeError(f'Migration marker missing: {marker}')

path.write_text(text, encoding='utf-8')
print('Goal timing buckets migrated')
