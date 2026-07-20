"""Kotlin receiver-typed member-call resolution (#1699)."""

from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _extract(tmp_path: Path, files: dict[str, str]) -> dict:
    paths = []
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        paths.append(path)
    return extract(paths, cache_root=tmp_path / "graphify-out", parallel=False)


def _find(result: dict, label: str, id_contains: str) -> str:
    return next(
        node["id"]
        for node in result["nodes"]
        if node.get("label") == label and id_contains in node["id"]
    )


def _call_edges(result: dict) -> list[dict]:
    return [edge for edge in result["edges"] if edge.get("relation") == "calls"]


def test_issue_1699_resolves_four_typed_receiver_shapes(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": (
            "class InputView {\n"
            "    fun updateKeyboardShow(show: Boolean) {}\n"
            "}\n"
        ),
        "PanelController.kt": (
            "class PanelController {\n"
            "    private val input = InputView()\n"
            "    fun getInputView(): InputView = input\n"
            "    fun onPanelClose() { input.updateKeyboardShow(false) }\n"
            "    fun onPanelOpen() { getInputView().updateKeyboardShow(true) }\n"
            "    fun onPanelToggle() {\n"
            "        val view = getInputView()\n"
            "        view.updateKeyboardShow(true)\n"
            "    }\n"
            "}\n"
        ),
        "Window.kt": (
            "fun open() {\n"
            "    val view = InputView()\n"
            "    view.updateKeyboardShow(true)\n"
            "}\n"
        ),
    })

    update = _find(result, ".updateKeyboardShow()", "inputview")
    callers = {
        _find(result, ".onPanelClose()", "panelcontroller"),
        _find(result, ".onPanelOpen()", "panelcontroller"),
        _find(result, ".onPanelToggle()", "panelcontroller"),
        _find(result, "open()", "window"),
    }
    resolved = {
        edge["source"]: edge
        for edge in _call_edges(result)
        if edge.get("target") == update and edge.get("source") in callers
    }
    assert set(resolved) == callers
    assert all(edge.get("confidence") == "INFERRED" for edge in resolved.values())
    assert all(edge.get("confidence_score") == 0.8 for edge in resolved.values())

    get_input = _find(result, ".getInputView()", "panelcontroller")
    assert any(
        edge["source"] in callers
        and edge["target"] == get_input
        and edge.get("confidence") == "EXTRACTED"
        for edge in _call_edges(result)
    )


def test_typed_receiver_selects_its_owner_not_same_named_local_method(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun updateKeyboardShow(show: Boolean) {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun updateKeyboardShow(show: Boolean) {}\n"
            "    fun run() { input.updateKeyboardShow(true) }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    expected = _find(result, ".updateKeyboardShow()", "inputview")
    wrong = _find(result, ".updateKeyboardShow()", "caller")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_ambiguous_receiver_type_emits_no_member_edge(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": "package a\nclass InputView { fun updateKeyboardShow() {} }\n",
        "b/InputView.kt": "package b\nclass InputView { fun updateKeyboardShow() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun run() { input.updateKeyboardShow() }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    assert not any(
        edge["source"] == run
        and "updatekeyboardshow" in str(edge["target"]).lower()
        for edge in _call_edges(result)
    )


def test_receiver_bindings_are_scoped_per_method(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Alpha.kt": "class Alpha { fun act() {} }\n",
        "Beta.kt": "class Beta { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun alpha() { val service = Alpha(); service.act() }\n"
            "    fun beta() { val service = Beta(); service.act() }\n"
            "}\n"
        ),
    })

    alpha = _find(result, ".alpha()", "caller")
    beta = _find(result, ".beta()", "caller")
    alpha_act = _find(result, ".act()", "alpha")
    beta_act = _find(result, ".act()", "beta")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (alpha, alpha_act) in calls
    assert (alpha, beta_act) not in calls
    assert (beta, beta_act) in calls
    assert (beta, alpha_act) not in calls


def test_parameter_shadow_does_not_reuse_field_type_but_this_field_does(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun updateKeyboardShow() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun shadowed(input: Any) { input.updateKeyboardShow() }\n"
            "    fun explicit(input: Any) { this.input.updateKeyboardShow() }\n"
            "    fun helper() {}\n"
            "    fun unqualified() { helper() }\n"
            "}\n"
        ),
    })

    shadowed = _find(result, ".shadowed()", "caller")
    explicit = _find(result, ".explicit()", "caller")
    unqualified = _find(result, ".unqualified()", "caller")
    helper = _find(result, ".helper()", "caller")
    update = _find(result, ".updateKeyboardShow()", "inputview")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (shadowed, update) not in calls
    assert (explicit, update) in calls
    assert (unqualified, helper) in calls


def test_lexical_binders_do_not_reuse_same_named_field_types(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val item = InputView()\n"
            "    private val error = InputView()\n"
            "    private val input = InputView()\n"
            "    private val explicit = InputView()\n"
            "    private val it = InputView()\n"
            "    private val anonymousInput = InputView()\n"
            "    private val whenInput = InputView()\n"
            "    fun loop(items: List<Any>) { for (item in items) { item.act() } }\n"
            "    fun caught() {\n"
            "        try { println(\"x\") } catch (error: Exception) { error.act() }\n"
            "    }\n"
            "    fun destructured(box: Pair<Any, Any>) {\n"
            "        val (unused, input) = box\n"
            "        input.act()\n"
            "    }\n"
            "    fun explicitLambda(items: List<Any>) {\n"
            "        items.forEach { explicit -> explicit.act() }\n"
            "    }\n"
            "    fun implicitLambda(items: List<Any>) { items.forEach { it.act() } }\n"
            "    fun anonymous() {\n"
            "        val block = fun(anonymousInput: Any) { anonymousInput.act() }\n"
            "    }\n"
            "    fun whenBound(other: Any) {\n"
            "        when (val whenInput = other) { else -> whenInput.act() }\n"
            "    }\n"
            "}\n"
        ),
    })

    act = _find(result, ".act()", "inputview")
    callers = {
        _find(result, f".{name}()", "caller")
        for name in (
            "loop",
            "caught",
            "destructured",
            "explicitLambda",
            "implicitLambda",
            "anonymous",
            "whenBound",
        )
    }
    assert not any(
        edge["source"] in callers and edge["target"] == act
        for edge in _call_edges(result)
    )


def test_type_parameters_do_not_resolve_to_same_named_concrete_class(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "GenericCaller.kt": (
            "class GenericCaller<InputView>(private val input: InputView) {\n"
            "    fun run() { input.act() }\n"
            "}\n"
        ),
        "FunctionCaller.kt": (
            "class FunctionCaller {\n"
            "    fun <InputView> run(input: InputView) { input.act() }\n"
            "}\n"
        ),
    })

    act = _find(result, ".act()", "inputview")
    callers = {
        _find(result, ".run()", "genericcaller"),
        _find(result, ".run()", "functioncaller"),
    }
    assert not any(
        edge["source"] in callers and edge["target"] == act
        for edge in _call_edges(result)
    )


def test_uppercase_factory_constructor_collision_is_left_unresolved(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView(val name: String) { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun InputView(value: Int): Other = Other()\n"
            "    fun factory() { val view = InputView(1); view.act() }\n"
            "    fun constructor() { val view = InputView(\"real\"); view.act() }\n"
            "    fun chained() { InputView(1).act() }\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, f".{name}()", "caller")
        for name in ("factory", "constructor", "chained")
    }
    input_act = _find(result, ".act()", "inputview")
    other_act = _find(result, ".act()", "other")
    assert not any(
        edge["source"] in callers and edge["target"] in {input_act, other_act}
        for edge in _call_edges(result)
    )


def test_top_level_factory_constructor_collision_is_left_unresolved(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView(val name: String) { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Factory.kt": "fun InputView(value: Int): Other = Other()\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun factory() { val view = InputView(1); view.act() }\n"
            "    fun constructor() { val view = InputView(\"real\"); view.act() }\n"
            "    fun chained() { InputView(1).act() }\n"
            "    fun explicit(view: InputView) { view.act() }\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, f".{name}()", "caller")
        for name in ("factory", "constructor", "chained")
    }
    input_act = _find(result, ".act()", "inputview")
    other_act = _find(result, ".act()", "other")
    assert not any(
        edge["source"] in callers and edge["target"] in {input_act, other_act}
        for edge in _call_edges(result)
    )
    explicit = _find(result, ".explicit()", "caller")
    assert any(
        edge["source"] == explicit and edge["target"] == input_act
        for edge in _call_edges(result)
    )


def test_same_file_extension_member_call_keeps_direct_edge(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Extensions.kt": (
            "class InputView\n"
            "fun InputView.decorate() {}\n"
            "fun run(view: InputView) { view.decorate() }\n"
        ),
    })

    run = _find(result, "run()", "extensions")
    decorate = _find(result, "decorate()", "extensions")
    edge = next(
        edge
        for edge in _call_edges(result)
        if edge["source"] == run and edge["target"] == decorate
    )
    assert edge["confidence"] == "EXTRACTED"


def test_constructor_provenance_survives_disjoint_same_named_bindings(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView(val name: String) { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Factory.kt": "fun InputView(value: Int): Other = Other()\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun getInputView(): InputView = InputView(\"real\")\n"
            "    fun run(flag: Boolean) {\n"
            "        if (flag) { val view = InputView(1); view.act() }\n"
            "        else { val view = getInputView() }\n"
            "    }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    input_act = _find(result, ".act()", "inputview")
    other_act = _find(result, ".act()", "other")
    assert not any(
        edge["source"] == run and edge["target"] in {input_act, other_act}
        for edge in _call_edges(result)
    )


def test_extension_this_uses_declared_receiver_not_enclosing_class(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputValue.kt": "class InputValue { fun act() {} }\n",
        "InputView.kt": "class InputView { val input = InputValue(); fun act() {} }\n",
        "CallerValue.kt": "class CallerValue { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    val input = CallerValue()\n"
            "    fun act() {}\n"
            "    fun InputView.extensionRun() { this.act() }\n"
            "    fun <InputView> InputView.genericRun() { this.act() }\n"
            "    fun InputView.propertyRun() { input.act() }\n"
            "}\n"
        ),
    })

    extension_run = _find(result, ".extensionRun()", "caller")
    generic_run = _find(result, ".genericRun()", "caller")
    property_run = _find(result, ".propertyRun()", "caller")
    caller_act = _find(result, ".act()", "caller")
    caller_value_act = _find(result, ".act()", "callervalue")
    input_act = _find(result, ".act()", "inputview")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (extension_run, input_act) in calls
    assert (extension_run, caller_act) not in calls
    assert (generic_run, input_act) not in calls
    assert (generic_run, caller_act) not in calls
    assert (property_run, caller_value_act) not in calls


def test_receiver_typed_calls_inside_lambdas_are_left_unresolved(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Other.kt": (
            "class OtherInput { fun act() {} }\n"
            "class Other { val input = OtherInput(); fun act() {} }\n"
        ),
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun act() {}\n"
            "    fun run(other: Other) { with(other) { this.act(); input.act() } }\n"
            "    fun direct() { this.act() }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    direct = _find(result, ".direct()", "caller")
    caller_act = _find(result, ".act()", "caller")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert not any(source == run and "act" in target for source, target in calls)
    assert (direct, caller_act) in calls


def test_overloaded_getter_with_different_return_types_is_ambiguous(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun isEmpty() = false }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun get(value: Int): InputView = InputView()\n"
            "    fun get(value: String): String = value\n"
            "    fun local() { val view = get(\"x\"); view.isEmpty() }\n"
            "    fun chained() { get(\"x\").isEmpty() }\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, ".local()", "caller"),
        _find(result, ".chained()", "caller"),
    }
    input_empty = _find(result, ".isEmpty()", "inputview")
    assert not any(
        edge["source"] in callers and edge["target"] == input_empty
        for edge in _call_edges(result)
    )


def test_lexical_callables_shadow_getter_and_constructor_inference(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun get(): InputView = InputView()\n"
            "    fun localFactory() {\n"
            "        fun InputView(): Other = Other()\n"
            "        val value = InputView()\n"
            "        value.act()\n"
            "        InputView().act()\n"
            "    }\n"
            "    fun callableParameter(get: () -> Other) {\n"
            "        val value = get()\n"
            "        value.act()\n"
            "        get().act()\n"
            "    }\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, ".localFactory()", "caller"),
        _find(result, ".callableParameter()", "caller"),
    }
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] in callers and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_lambda_shadow_does_not_poison_later_outer_receiver_call(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun run(items: List<Any>) {\n"
            "        items.forEach { input -> input.toString() }\n"
            "        input.act()\n"
            "    }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    act = _find(result, ".act()", "inputview")
    assert any(
        edge["source"] == run and edge["target"] == act
        for edge in _call_edges(result)
    )


def test_inner_block_shadow_does_not_poison_later_outer_receiver_call(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    private val input = InputView()\n"
            "    fun run(flag: Boolean) {\n"
            "        if (flag) {\n"
            "            val input = Other()\n"
            "            input.act()\n"
            "        }\n"
            "        input.act()\n"
            "    }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    expected = _find(result, ".act()", "inputview")
    wrong = _find(result, ".act()", "other")
    calls = [
        edge
        for edge in _call_edges(result)
        if edge["source"] == run and edge["target"] in {expected, wrong}
    ]
    assert [edge["target"] for edge in calls] == [expected]


def test_labeled_this_member_calls_are_left_unresolved(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Outer.kt": (
            "class Outer {\n"
            "    fun act() {}\n"
            "    inner class Inner {\n"
            "        fun act() {}\n"
            "        fun run() { this@Outer.act() }\n"
            "    }\n"
            "}\n"
        ),
        "Caller.kt": (
            "class Caller {\n"
            "    fun act() {}\n"
            "    fun InputView.runExtension() { this@Caller.act() }\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, ".run()", "inner"),
        _find(result, ".runExtension()", "caller"),
    }
    acts = {
        _find(result, ".act()", "outer"),
        _find(result, ".act()", "inner"),
        _find(result, ".act()", "caller"),
        _find(result, ".act()", "inputview"),
    }
    assert not any(
        edge["source"] in callers and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_qualified_receiver_type_selects_exact_package(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": "package a\nclass InputView { fun act() {} }\n",
        "b/InputView.kt": "package b\nclass InputView { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    fun run(view: a.InputView) { view.act() }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    expected = _find(result, ".act()", "a_inputview")
    wrong = _find(result, ".act()", "b_inputview")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_unrelated_package_factory_does_not_hide_local_constructor(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": (
            "package a\n"
            "class InputView { fun act() {} }\n"
        ),
        "a/Caller.kt": (
            "package a\n"
            "class Caller { fun run() { val view = InputView(); view.act() } }\n"
        ),
        "b/Factory.kt": (
            "package b\n"
            "class Other { fun act() {} }\n"
            "fun InputView(): Other = Other()\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    expected = _find(result, ".act()", "a_inputview")
    wrong = _find(result, ".act()", "b_factory_other")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_imported_factory_collision_remains_unresolved(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": (
            "package a\n"
            "class InputView { fun act() {} }\n"
        ),
        "a/Caller.kt": (
            "package a\n"
            "import b.InputView\n"
            "class Caller { fun run() { val view = InputView(); view.act() } }\n"
        ),
        "b/Factory.kt": (
            "package b\n"
            "class Other { fun act() {} }\n"
            "fun InputView(): Other = Other()\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    acts = {
        _find(result, ".act()", "a_inputview"),
        _find(result, ".act()", "b_factory_other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_wildcard_and_alias_imported_factories_remain_unresolved(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": (
            "package a\n"
            "class InputView { fun act() {} }\n"
        ),
        "a/StarCaller.kt": (
            "package a\n"
            "import b.*\n"
            "class StarCaller { fun run() { val view = InputView(); view.act() } }\n"
        ),
        "a/AliasCaller.kt": (
            "package a\n"
            "import c.Factory as InputView\n"
            "class AliasCaller { fun run() { val view = InputView(); view.act() } }\n"
        ),
        "b/Factory.kt": (
            "package b\n"
            "class Other { fun act() {} }\n"
            "fun InputView(): Other = Other()\n"
        ),
        "c/Factory.kt": (
            "package c\n"
            "class Another { fun act() {} }\n"
            "fun Factory(): Another = Another()\n"
        ),
    })

    callers = {
        _find(result, ".run()", "starcaller"),
        _find(result, ".run()", "aliascaller"),
    }
    acts = {
        _find(result, ".act()", "a_inputview"),
        _find(result, ".act()", "b_factory_other"),
        _find(result, ".act()", "c_factory_another"),
    }
    assert not any(
        edge["source"] in callers and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_import_alias_resolves_receiver_to_imported_type(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": "package a\nclass InputView { fun act() {} }\n",
        "b/View.kt": "package b\nclass View { fun act() {} }\n",
        "c/Caller.kt": (
            "package c\n"
            "import a.InputView as View\n"
            "class Caller { fun run(view: View) { view.act() } }\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    expected = _find(result, ".act()", "a_inputview")
    wrong = _find(result, ".act()", "b_view")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_unresolved_typealias_does_not_fall_back_to_unrelated_java_type(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": "package a\nclass InputView { fun act() {} }\n",
        "b/View.java": (
            "package b;\n"
            "public class View { public void act() {} }\n"
        ),
        "c/Caller.kt": (
            "package c\n"
            "typealias View = a.InputView\n"
            "class Caller { fun run(view: View) { view.act() } }\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    wrong = _find(result, ".act()", "b_view")
    assert not any(
        edge["source"] == run and edge["target"] == wrong
        for edge in _call_edges(result)
    )


def test_callable_class_property_shadows_constructor_inference(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Caller.kt": (
            "class Caller {\n"
            "    val InputView: () -> Other = { Other() }\n"
            "    fun run() {\n"
            "        val value = InputView()\n"
            "        value.act()\n"
            "        InputView().act()\n"
            "    }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_same_package_callable_property_shadows_imported_constructor(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "a/InputView.kt": (
            "package a\n"
            "class InputView { fun act() {} }\n"
        ),
        "c/Factory.kt": (
            "package c\n"
            "class Other { fun act() {} }\n"
            "val InputView: () -> Other = { Other() }\n"
        ),
        "c/Caller.kt": (
            "package c\n"
            "import a.*\n"
            "class Caller { fun run() { val value = InputView(); value.act() } }\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    acts = {
        _find(result, ".act()", "a_inputview"),
        _find(result, ".act()", "c_factory_other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_callable_object_is_not_treated_as_constructor(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "class Other { fun act() {} }\n"
            "object InputView {\n"
            "    operator fun invoke(): Other = Other()\n"
            "    fun act() {}\n"
            "}\n"
            "fun run() { val value = InputView(); value.act() }\n"
        ),
    })

    run = _find(result, "run()", "calls")
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_only_companion_operator_invoke_shadows_constructor(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Operator.kt": (
            "class Other { fun act() {} }\n"
            "class InputView private constructor(value: Int) {\n"
            "    companion object {\n"
            "        operator fun invoke(): Other = Other()\n"
            "    }\n"
            "    fun act() {}\n"
            "}\n"
            "fun run() { val value = InputView(); value.act() }\n"
        ),
        "Plain.kt": (
            "class PlainView {\n"
            "    companion object { fun invoke() = Unit }\n"
            "    fun act() {}\n"
            "}\n"
            "fun plain() { val value = PlainView(); value.act() }\n"
        ),
    })

    run = _find(result, "run()", "operator")
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )
    plain = _find(result, "plain()", "plain")
    plain_act = _find(result, ".act()", "plainview")
    assert any(
        edge["source"] == plain and edge["target"] == plain_act
        for edge in _call_edges(result)
    )


def test_unresolved_implicit_receivers_block_constructor_inference(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Other.kt": "class Other { fun act() {} }\n",
        "Inherited.kt": (
            "open class Base { val InputView: () -> Other = { Other() } }\n"
            "class Caller : Base() {\n"
            "    fun run() { val value = InputView(); value.act() }\n"
            "}\n"
        ),
        "Extension.kt": (
            "class Scope { val InputView: () -> Other = { Other() } }\n"
            "fun Scope.extensionRun() {\n"
            "    val value = InputView()\n"
            "    value.act()\n"
            "}\n"
        ),
    })

    callers = {
        _find(result, ".run()", "caller"),
        _find(result, "extensionRun()", "extension"),
    }
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] in callers and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_companion_callable_shadows_constructor_inference(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "class InputView {\n"
            "    fun act() {}\n"
            "}\n"
            "class Other {\n"
            "    fun act() {}\n"
            "}\n"
            "class Caller {\n"
            "    companion object {\n"
            "        val InputView: () -> Other = { Other() }\n"
            "    }\n"
            "    fun run() {\n"
            "        val value = InputView()\n"
            "        value.act()\n"
            "    }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "caller")
    acts = {
        _find(result, ".act()", "inputview"),
        _find(result, ".act()", "other"),
    }
    assert not any(
        edge["source"] == run and edge["target"] in acts
        for edge in _call_edges(result)
    )


def test_member_method_wins_over_same_named_unrelated_extension(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "InputView.kt": "class InputView { fun act() {} }\n",
        "Calls.kt": (
            "class Other\n"
            "fun Other.act() {}\n"
            "fun run(view: InputView) { view.act() }\n"
        ),
    })

    run = _find(result, "run()", "calls")
    expected = _find(result, ".act()", "inputview")
    wrong = _find(result, "act()", "calls")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_companion_extension_is_not_visible_as_top_level(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "class InputView { fun decorate() {} }\n"
            "class Host {\n"
            "    companion object {\n"
            "        fun InputView.decorate() {}\n"
            "    }\n"
            "}\n"
            "fun run(value: InputView) { value.decorate() }\n"
        ),
    })

    run = _find(result, "run()", "calls")
    expected = _find(result, ".decorate()", "inputview")
    wrong = _find(result, "decorate()", "calls")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_member_extension_arity_collision_is_left_unresolved(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "class InputView { fun act(value: Int) {} }\n"
            "fun InputView.act() {}\n"
            "fun run(view: InputView) { view.act() }\n"
        ),
    })

    run = _find(result, "run()", "calls")
    member = _find(result, ".act()", "inputview")
    extension = _find(result, "act()", "calls")
    assert not any(
        edge["source"] == run and edge["target"] in {member, extension}
        for edge in _call_edges(result)
    )


def test_extension_does_not_override_possible_inherited_member(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "open class Base { fun act() {} }\n"
            "class Child : Base()\n"
            "fun Child.act() {}\n"
            "fun run(value: Child) { value.act() }\n"
        ),
    })

    run = _find(result, "run()", "calls")
    extension = _find(result, "act()", "calls")
    assert not any(
        edge["source"] == run and edge["target"] == extension
        for edge in _call_edges(result)
    )


def test_member_extension_resolves_inside_its_dispatch_owner(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Extensions.kt": (
            "class InputView\n"
            "class Host {\n"
            "    fun InputView.decorate() {}\n"
            "    fun run(view: InputView) { view.decorate() }\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "host")
    decorate = _find(result, ".decorate()", "host")
    assert any(
        edge["source"] == run
        and edge["target"] == decorate
        and edge.get("confidence") == "EXTRACTED"
        for edge in _call_edges(result)
    )


def test_inherited_member_and_member_extension_remain_resolvable(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "class InputView\n"
            "interface Extensions {\n"
            "    fun InputView.decorate()\n"
            "}\n"
            "interface PrivateExtensions {\n"
            "    fun InputView.hidden()\n"
            "}\n"
            "open class Host {\n"
            "    fun inherited() {}\n"
            "    open fun InputView.decorate() {}\n"
            "    private fun InputView.hidden() {}\n"
            "}\n"
            "class Child : Host() {\n"
            "    override fun InputView.decorate() {}\n"
            "    fun run(view: InputView) {\n"
            "        inherited()\n"
            "        view.decorate()\n"
            "    }\n"
            "}\n"
            "class Sibling : Host(), Extensions {\n"
            "    fun siblingRun(view: InputView) { view.decorate() }\n"
            "}\n"
            "abstract class PrivateChild : Host(), PrivateExtensions {\n"
            "    fun privateRun(view: InputView) { view.hidden() }\n"
            "}\n"
            "class Outer {\n"
            "    private fun InputView.outerDecorate() {}\n"
            "    inner class Inner {\n"
            "        fun innerRun(view: InputView) { view.outerDecorate() }\n"
            "    }\n"
            "}\n"
            "fun typed(value: Child) { value.inherited() }\n"
        ),
    })

    run = _find(result, ".run()", "child")
    typed = _find(result, "typed()", "calls")
    inherited = _find(result, ".inherited()", "host")
    host_decorate = _find(result, ".decorate()", "host")
    child_decorate = _find(result, ".decorate()", "child")
    sibling_run = _find(result, ".siblingRun()", "sibling")
    private_run = _find(result, ".privateRun()", "privatechild")
    private_host = _find(result, ".hidden()", "host")
    private_interface = _find(result, ".hidden()", "privateextensions")
    inner_run = _find(result, ".innerRun()", "inner")
    outer_decorate = _find(result, ".outerDecorate()", "outer")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, inherited) in calls
    assert (typed, inherited) in calls
    assert (run, child_decorate) in calls
    assert (run, host_decorate) not in calls
    assert (sibling_run, host_decorate) in calls
    assert (private_run, private_interface) in calls
    assert (private_run, private_host) not in calls
    assert (inner_run, outer_decorate) in calls


def test_super_and_object_qualified_calls_keep_existing_resolution(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Calls.kt": (
            "interface Action {\n"
            "    fun act()\n"
            "}\n"
            "open class Base {\n"
            "    open fun act() {}\n"
            "}\n"
            "class Child : Base(), Action {\n"
            "    override fun act() {}\n"
            "    fun run() { super.act() }\n"
            "}\n"
            "class TypedChild : Base(), Action\n"
            "object InputView {\n"
            "    fun show() {}\n"
            "}\n"
            "class Toolbar {\n"
            "    companion object {\n"
            "        fun render() {}\n"
            "        private fun secret() {}\n"
            "    }\n"
            "    class Nested {\n"
            "        fun nestedRun() { Toolbar.secret() }\n"
            "    }\n"
            "}\n"
            "fun open(value: TypedChild) {\n"
            "    value.act()\n"
            "    InputView.show()\n"
            "    Toolbar.render()\n"
            "}\n"
        ),
    })

    run = _find(result, ".run()", "child")
    act = _find(result, ".act()", "base")
    open_call = _find(result, "open()", "calls")
    show = _find(result, ".show()", "inputview")
    render = _find(result, "render()", "calls")
    nested_run = _find(result, ".nestedRun()", "nested")
    secret = _find(result, "secret()", "calls")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, act) in calls
    assert (open_call, act) in calls
    assert (open_call, show) in calls
    assert (open_call, render) in calls
    assert (nested_run, secret) in calls


def test_nested_receiver_type_resolves_with_enclosing_type_name(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Nested.kt": (
            "package a\n"
            "class Outer {\n"
            "    class InputView { fun act() {} }\n"
            "}\n"
            "fun run(value: Outer.InputView) { value.act() }\n"
        ),
    })

    run = _find(result, "run()", "nested")
    act = _find(result, ".act()", "inputview")
    assert any(
        edge["source"] == run and edge["target"] == act
        for edge in _call_edges(result)
    )


def test_imported_enclosing_type_resolves_nested_receiver(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "Default.kt": (
            "class Outer {\n"
            "    class InputView {\n"
            "        fun act() {}\n"
            "    }\n"
            "}\n"
        ),
        "a/Outer.kt": (
            "package a\n"
            "class Outer {\n"
            "    class InputView {\n"
            "        fun act() {}\n"
            "    }\n"
            "}\n"
        ),
        "c/Caller.kt": (
            "package c\n"
            "import a.Outer\n"
            "fun run(value: Outer.InputView) { value.act() }\n"
        ),
    })

    run = _find(result, "run()", "caller")
    expected = _find(result, ".act()", "a_outer_inputview")
    wrong = _find(result, ".act()", "default_inputview")
    calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
    assert (run, expected) in calls
    assert (run, wrong) not in calls


def test_aliased_extension_import_resolves_local_call_name(tmp_path: Path) -> None:
    result = _extract(tmp_path, {
        "a/Outer.kt": (
            "package a\n"
            "class Outer {\n"
            "    class InputView\n"
            "}\n"
        ),
        "b/Extensions.kt": (
            "package b\n"
            "import a.Outer\n"
            "fun Outer.InputView.decorate() {}\n"
        ),
        "c/Caller.kt": (
            "package c\n"
            "import a.Outer\n"
            "import b.decorate as adorn\n"
            "fun run(view: Outer.InputView) { view.adorn() }\n"
        ),
    })

    run = _find(result, "run()", "caller")
    decorate = _find(result, "decorate()", "extensions")
    assert any(
        edge["source"] == run and edge["target"] == decorate
        for edge in _call_edges(result)
    )


def test_kotlin_resolution_markers_do_not_leak_to_public_nodes(
    tmp_path: Path,
) -> None:
    result = _extract(tmp_path, {
        "Extensions.kt": (
            "class InputView\n"
            "fun InputView.decorate() {}\n"
            "fun run(view: InputView) { view.decorate() }\n"
        ),
    })

    assert all(
        "_kotlin_extension_receiver_type" not in node
        for node in result["nodes"]
    )


def test_package_resolution_survives_portable_ast_cache(tmp_path: Path) -> None:
    shared_cache = tmp_path / "shared-cache"

    def extract_corpus(root: Path) -> dict:
        files = {
            "a/InputView.kt": "package a\nclass InputView { fun act() {} }\n",
            "b/InputView.kt": "package b\nclass InputView { fun act() {} }\n",
            "Caller.kt": (
                "class Caller { fun run(view: a.InputView) { view.act() } }\n"
            ),
        }
        paths = []
        for name, body in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            paths.append(path)
        return extract(paths, cache_root=shared_cache, parallel=False)

    fresh = extract_corpus(tmp_path / "repo-a")
    cached = extract_corpus(tmp_path / "repo-b")

    for result in (fresh, cached):
        run = _find(result, ".run()", "caller")
        expected = _find(result, ".act()", "a_inputview")
        wrong = _find(result, ".act()", "b_inputview")
        calls = {(edge["source"], edge["target"]) for edge in _call_edges(result)}
        assert (run, expected) in calls
        assert (run, wrong) not in calls
