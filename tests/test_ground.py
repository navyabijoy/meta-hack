from backend.pipeline import ground

SAM_TWO_X = (
    "<0f>0<|box;x1=878;y1=252;x2=886;y2=260;w=1280;h=800|>"
    "<0f>1<|box;x1=878;y1=300;x2=886;y2=309;w=1280;h=800|>"
)


def test_parse_boxes_and_masks():
    raw = "<0f>0<|box;x1=560;y1=80;x2=700;y2=120;w=800;h=500|><|mask;x=0;y=0;data=41,141,~abc!def|>"
    inst = ground.parse(raw)
    assert len(inst) == 1
    assert inst[0].box == (560, 80, 700, 120)
    assert inst[0].mask == {"h": 41, "w": 141, "enc": "~", "payload": "abc!def"}


def test_parse_empty():
    assert ground.parse("") == []


def test_dist_to_box():
    assert ground.dist_to_box((5, 5), (0, 0, 10, 10)) == 0
    assert ground.dist_to_box((13, 14), (0, 0, 10, 10)) == 5


def _fake_sam(responses):
    def sam_image(path, prompt):
        return responses.get(prompt, "")
    return sam_image


def test_picks_instance_under_cursor_and_ordinal(monkeypatch):
    monkeypatch.setattr(ground.meta, "sam_image", _fake_sam({"x icon": SAM_TWO_X}))
    step = {"id": 10, "frame": 420, "target": {"sam_prompts": ["x icon"]}}
    g = ground.ground_step(step, "frame.jpg", cursor=(880, 304))
    assert g.ordinal == 1 and g.count == 2  # the lower x: second from the top
    assert g.confidence == "high"


def test_same_row_rule(monkeypatch):
    checkboxes = (
        "<0f>0<|box;x1=387;y1=251;x2=400;y2=263;w=1280;h=800|>"
        "<0f>1<|box;x1=387;y1=299;x2=401;y2=311;w=1280;h=800|>"
    )
    monkeypatch.setattr(ground.meta, "sam_image", _fake_sam({"checkbox": checkboxes}))
    step = {"id": 8, "frame": 360, "target": {"sam_prompts": ["checkbox"]}}
    # Cursor is on the task's text, far right of the checkbox but in its row.
    g = ground.ground_step(step, "frame.jpg", cursor=(498, 255))
    assert g.ordinal == 0 and g.confidence == "medium"
    assert "same row" in g.reason


def test_unique_match_without_cursor(monkeypatch):
    save = "<0f>0<|box;x1=822;y1=132;x2=899;y2=175;w=1280;h=800|>"
    monkeypatch.setattr(ground.meta, "sam_image", _fake_sam({"black save button": save}))
    step = {"id": 3, "frame": 108, "target": {"sam_prompts": ["black save button"]}}
    g = ground.ground_step(step, "frame.jpg", cursor=None)
    assert g.ordinal == 0 and g.count == 1


def test_nothing_found_is_low_confidence(monkeypatch):
    monkeypatch.setattr(ground.meta, "sam_image", _fake_sam({}))
    step = {"id": 1, "frame": 1, "target": {"sam_prompts": ["widget"]}}
    g = ground.ground_step(step, "frame.jpg", cursor=(10, 10))
    assert g.target is None and g.confidence == "low"
