from charter_parser.models import Clause
from charter_parser.validators import duplicate_ids, order_violations


def test_duplicate_ids_and_order_violations():
    clauses = [
        Clause(order=1, section="x", local_num=1, id="x:1", title="", text="a", page_start=0, page_end=0),
        Clause(order=1, section="x", local_num=2, id="x:1", title="", text="b", page_start=0, page_end=0),
    ]
    assert duplicate_ids(clauses) == ["x:1"]
    assert order_violations(clauses) == ["x:1"]
