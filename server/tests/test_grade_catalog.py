import pytest
from pocketclaw.grade_catalog import CATEGORIES, GRADE_LABELS, get_catalog
from pocketclaw.notes import PRESET_IDIOMS
from pocketclaw.practice import CORE_POEMS

@pytest.mark.parametrize('category', CATEGORIES)
def test_all_none_and_multiple_grades(category):
    all_items = get_catalog(category)
    assert get_catalog(category, []) == []
    assert get_catalog(category, list(range(1, 7))) == all_items
    assert get_catalog(category, [2, 5, 2]) == [item for item in all_items if item['grade'] in (2, 5)]
    assert len({item['id'] for item in all_items}) == len(all_items)
    for grade in GRADE_LABELS:
        assert len(get_catalog(category, [grade])) >= 7

@pytest.mark.parametrize('grades', [[0], [7], ['3'], [True], '3'])
def test_invalid_grades_rejected(grades):
    with pytest.raises(ValueError): get_catalog('words', grades)

def test_unknown_category_is_rejected():
    with pytest.raises(ValueError): get_catalog('not-a-category')

def test_records_are_detached_from_existing_data():
    poems, idioms = get_catalog('poems'), get_catalog('idioms')
    poem = next(item for item in poems if item['title'] == CORE_POEMS[0].title)
    idiom = next(item for item in idioms if item['title'] == PRESET_IDIOMS[0]['idiom'])
    original_line, original_meaning = CORE_POEMS[0].lines[0], PRESET_IDIOMS[0]['meaning']
    poem['lines'][0] = 'changed'; idiom['meaning'] = 'changed'
    assert CORE_POEMS[0].lines[0] == original_line
    assert PRESET_IDIOMS[0]['meaning'] == original_meaning
    assert next(x for x in get_catalog('poems') if x['title'] == poem['title'])['lines'][0] == original_line

def test_new_catalog_content_has_material_and_consistent_shape():
    assert len(get_catalog('words')) >= 180
    assert len(get_catalog('idioms')) >= 48
    assert len(get_catalog('poems')) >= len(CORE_POEMS) + 6
    for item in get_catalog('poems'):
        assert item['author'] and item['dynasty'] and len(item['lines']) >= 4
    for item in get_catalog('idioms'):
        assert item['idiom'] and item['meaning']
