import json
import sys
from collections import defaultdict
from typing import Any, List, Dict

def _collect_steps_anywhere(node: Any) -> List[Dict]:
    """
    Recursively walk the JSON tree and collect any list under a key named 'steps'
    whose elements look like step objects (have at least an _id).
    """
    found: List[Dict] = []

    if isinstance(node, dict):
        for key, value in node.items():
            if key == "steps" and isinstance(value, list):
                step_like = [
                    item for item in value
                    if isinstance(item, dict) and (item.get("_id") or item.get("id"))
                ]
                if step_like:
                    found.extend(step_like)

            found.extend(_collect_steps_anywhere(value))

    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_steps_anywhere(item))

    return found


def load_steps(data: Any) -> List[Dict]:
    """
    Collect ALL step objects from anywhere in the JSON tree, then dedupe by ID.
    """
    all_steps = _collect_steps_anywhere(data)

    by_id: Dict[str, Dict] = {}
    for s in all_steps:
        sid = s.get("_id") or s.get("id")
        if sid and sid not in by_id:
            by_id[sid] = s

    return list(by_id.values())


def load_triggers(data: Any) -> List[Dict]:
    if isinstance(data, dict) and isinstance(data.get("triggers"), list):
        return data["triggers"]
    if isinstance(data, list):
        return data
    raise ValueError("Could not find a 'triggers' list in the JSON.")


def load_widgets(data: Any) -> List[Dict]:
    widgets = data.get("widgets") if isinstance(data, dict) else None
    if isinstance(widgets, list):
        return widgets
    return []


def index_by_id(items: List[Dict]) -> Dict[str, Dict]:
    idx: Dict[str, Dict] = {}
    for item in items:
        _id = item.get("_id") or item.get("id")
        if _id:
            idx[_id] = item
    return idx


def find_step(steps: List[Dict], step_id: str) -> Dict:
    for s in steps:
        if s.get("_id") == step_id or s.get("id") == step_id:
            return s
    return None


def get_trigger_event_type(trigger: Dict) -> str:
    """
    Try a few common fields to classify the trigger's event.
    First look at trigger['event']['type'], then fall back to flat fields.
    """
    ev = trigger.get("event")
    if isinstance(ev, dict):
        t = ev.get("type")
        if isinstance(t, str):
            return t

    for key in ("eventType", "event_type", "triggerEventType", "type"):
        if key in trigger and isinstance(trigger[key], str):
            return trigger[key]

    return "UNKNOWN"


def categorize_step_triggers(trigger_objs: List[Dict]) -> Dict[str, List[Dict]]:
    cats: Dict[str, List[Dict]] = {
        "step_open": [],
        "interval": [],
        "machines_output": [],
        "step_closed": [],
        "other": [],
    }

    for trig in trigger_objs:
        if trig is None:
            continue

        et = get_trigger_event_type(trig)
        et_lower = et.lower()

        if "step_open" in et_lower or "step_enter" in et_lower:
            cats["step_open"].append(trig)

        elif "step_closed" in et_lower or "step_close" in et_lower or "step_exit" in et_lower:
            cats["step_closed"].append(trig)

        elif "interval" in et_lower or "timer" in et_lower:
            cats["interval"].append(trig)

        elif "machines_output" in et_lower or "machine" in et_lower or "device" in et_lower:
            cats["machines_output"].append(trig)

        else:
            cats["other"].append(trig)

    return cats


def summarize_input_values(input_values: List[Dict]) -> str:
    pieces = []
    for iv in input_values:
        ds = iv.get("datasourceType") or iv.get("datasource_type") or "?"
        path = iv.get("path") or []
        slot = iv.get("dataModelSlot") or iv.get("data_model_slot")
        var  = iv.get("variableId") or iv.get("variable_id")
        expr = iv.get("exprStr") or iv.get("expression")
        step_target = iv.get("stepId") or iv.get("step_id")

        desc_bits = [f"source={ds}"]
        if path:
            desc_bits.append(f"path={'/'.join(path)}")
        if slot:
            desc_bits.append(f"slot={slot}")
        if var:
            desc_bits.append(f"var={var}")
        if expr:
            desc_bits.append(f"expr={expr[:40]}...")
        if step_target:
            desc_bits.append(f"stepId={step_target}")

        pieces.append("[" + ", ".join(desc_bits) + "]")

    return ", ".join(pieces) if pieces else "(no inputs)"


def print_trigger_details(trigger: Dict, indent: str = "  ") -> None:
    trig_id = trigger.get("_id") or trigger.get("id")
    label = (
        trigger.get("description")
        or trigger.get("label")
        or trigger.get("name")
        or "(no description)"
    )

    etype = get_trigger_event_type(trigger)
    cond_type = trigger.get("conditionType") or trigger.get("booleanOperator") or trigger.get("logicalOperator")

    print(f"{indent}- Trigger {trig_id}: {label}")
    print(f"{indent}  Event type: {etype}")
    if cond_type:
        print(f"{indent}  Condition logic: {cond_type}")

    clauses = trigger.get("clauses", [])
    if not clauses:
        print(f"{indent}  (No clauses)")
        return

    for idx, clause in enumerate(clauses, start=1):
        clause_logic = (
            clause.get("type")
            or clause.get("logicalOperator")
            or clause.get("booleanOperator")
        )

        header = f"{indent}  Clause {idx}:"
        if clause_logic:
            header += f" (logic: {clause_logic})"
        print(header)

        # ---------- IF / conditions ----------
        conditions = clause.get("conditions", [])

        if not conditions:
            print(f"{indent}    IF: (no conditions)")
        else:
            print(f"{indent}    IF conditions (count: {len(conditions)}):")
            for c_idx, cond in enumerate(conditions, start=1):
                c_type = cond.get("type") or cond.get("conditionType")

                left = (
                    cond.get("left")
                    or cond.get("lhs")
                    or cond.get("field")
                    or cond.get("path")
                )
                op = cond.get("operator") or cond.get("op")
                right = (
                    cond.get("right")
                    or cond.get("rhs")
                    or cond.get("value")
                )

                def short(v):
                    if v is None:
                        return None
                    if isinstance(v, str):
                        return v
                    text = json.dumps(v)
                    return text if len(text) <= 60 else text[:57] + "..."

                left_s = short(left)
                right_s = short(right)

                pieces = []
                if c_type:
                    pieces.append(f"type={c_type}")
                if left_s:
                    pieces.append(f"LHS={left_s}")
                if op:
                    pieces.append(f"op={op}")
                if right_s:
                    pieces.append(f"RHS={right_s}")

                if pieces:
                    print(f"{indent}      {c_idx}. " + ", ".join(pieces))
                else:
                    raw = short(cond)
                    print(f"{indent}      {c_idx}. (raw) {raw}")

        # ---------- THEN / actions ----------
        actions = clause.get("actions", [])
        if not actions:
            print(f"{indent}    THEN: (no actions)")
        else:
            print(f"{indent}    THEN actions:")
            for a_idx, action in enumerate(actions, start=1):
                atype = action.get("type") or action.get("actionType") or "UNKNOWN_ACTION"
                aname = action.get("name") or ""

                is_transition = action.get("is_transition") or action.get("isTransition")
                transition_tag = " [TRANSITION]" if is_transition else ""

                ivs = action.get("input_values", []) or action.get("inputs", [])
                iv_summary = summarize_input_values(ivs)
                label_str = f" ({aname})" if aname else ""

                print(
                    f"{indent}      {a_idx}. "
                    f"{atype}{label_str}{transition_tag} -> {iv_summary}"
                )


def print_trigger_category(title: str, triggers: List[Dict]) -> None:
    print(f"\n=== {title} ({len(triggers)}) ===")
    if not triggers:
        print("  (none)")
        return
    for trig in triggers:
        print_trigger_details(trig, indent="  ")

def get_widget_type(widget: Dict) -> str:
    for key in ("type", "widgetType", "kind", "widget_type"):
        if key in widget and isinstance(widget[key], str):
            return widget[key]
    return "UnknownWidgetType"


def is_button_widget(widget: Dict) -> bool:
    wtype = get_widget_type(widget).lower()
    if "button" in wtype:
        return True
    return False


def get_widget_name(widget: Dict) -> str:
    return widget.get("name") or widget.get("label") or "(no name)"


def get_button_text(widget: Dict) -> str:
    """
    Try a few common places where button text might live.
    """
    if widget.get("text"):
        return widget["text"]
    if widget.get("label"):
        return widget["label"]

    for key in ("props", "properties", "config", "options"):
        props = widget.get(key)
        if isinstance(props, dict):
            for tkey in ("text", "label", "buttonText", "caption"):
                if props.get(tkey):
                    return props[tkey]

    return None


def group_widgets_by_type(widget_objs: List[Dict]) -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for w in widget_objs:
        if w is None:
            continue
        grouped[get_widget_type(w)].append(w)
    return grouped


def get_widget_trigger_ids(widget: Dict[str, Any]) -> List[str]:
    """
    Extract trigger IDs from a widget.

    Tries common patterns:
      - widget['triggers'] = [id, id, ...]
      - widget['triggerIds'] / widget['trigger_ids']
      - nested under props/config/options
    """
    ids: List[str] = []

    for key in ("triggers", "triggerIds", "trigger_ids"):
        val = widget.get(key)
        if isinstance(val, list):
            ids.extend([t for t in val if isinstance(t, str)])

    # Nested inside props/config/etc
    for container_key in ("props", "properties", "config", "options"):
        container = widget.get(container_key)
        if isinstance(container, dict):
            for key in ("triggers", "triggerIds", "trigger_ids"):
                val = container.get(key)
                if isinstance(val, list):
                    ids.extend([t for t in val if isinstance(t, str)])

    # Dedupe while preserving order
    seen = set()
    deduped: List[str] = []
    for tid in ids:
        if tid not in seen:
            seen.add(tid)
            deduped.append(tid)

    return deduped


def print_widget_groups(grouped: Dict[str, List[Dict]], trig_index: Dict[str, Dict]) -> None:
    print("\n================ Widgets with triggers on this step ================")
    if not grouped:
        print("  (no widget-attached triggers on this step)")
        return

    for wtype, widgets in grouped.items():
        print(f"\nWidget type: {wtype} (count: {len(widgets)})")
        for w in widgets:
            wid = w.get("_id") or w.get("id")
            name = get_widget_name(w)

            trigger_ids = get_widget_trigger_ids(w)
            trigger_summaries = []
            for tid in trigger_ids:
                trig = trig_index.get(tid)
                if trig:
                    tlabel = (
                        trig.get("description")
                        or trig.get("label")
                        or trig.get("name")
                        or ""
                    )
                    trigger_summaries.append(f"{tid} ({tlabel})")
                else:
                    trigger_summaries.append(tid)

            triggers_str = ", ".join(trigger_summaries) if trigger_summaries else "no triggers?"

            if is_button_widget(w):
                btext = get_button_text(w)
                if btext:
                    print(f"  - {wid}: {name} [BUTTON text: '{btext}'] -> triggers: {triggers_str}")
                else:
                    print(f"  - {wid}: {name} [BUTTON] -> triggers: {triggers_str}")
            else:
                print(f"  - {wid}: {name} -> triggers: {triggers_str}")

# ---------- Helpers for mermaid trigger expansion ----------

def load_table_queries(data: Any) -> List[Dict]:
    """
    Try to find table-aggregation query objects anywhere in the JSON.
    Prefer a top-level 'table_queries' (or similar) list, but fall back
    to a recursive scan for objects that look like queries.
    """
    tqs: List[Dict] = []

    # Direct top-level keys first
    if isinstance(data, dict):
        for key in ("table_queries", "tableQueries", "app_table_queries"):
            val = data.get(key)
            if isinstance(val, list):
                tqs.extend(val)

    # If we didn't find any, do a recursive scan
    if not tqs:
        def _collect(node: Any) -> List[Dict]:
            found: List[Dict] = []
            if isinstance(node, dict):
                for _k, v in node.items():
                    if isinstance(v, list):
                        if v and all(isinstance(it, dict) for it in v):
                            if any("aggregations" in it and "label" in it for it in v):
                                found.extend(v)
                    found.extend(_collect(v))
            elif isinstance(node, list):
                for item in node:
                    found.extend(_collect(item))
            return found

        tqs = _collect(data)

    return tqs


def load_variables(data: Any) -> List[Dict]:
    vars_ = data.get("variables") if isinstance(data, dict) else None
    if isinstance(vars_, list):
        return vars_
    return []


def load_data_model_slots(data: Any) -> List[Dict]:
    dms: List[Dict] = []
    if isinstance(data, dict):
        for key in ("data_model_slots", "dataModelSlots"):
            val = data.get(key)
            if isinstance(val, list):
                dms.extend(val)
    return dms


def describe_input_value(iv: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    ds = iv.get("datasourceType")

    if ds == "variable":
        var_id = iv.get("variableId") or iv.get("variable")
        if var_id:
            var = ctx["var_index"].get(var_id)
            name = var.get("name") if var else var_id
            return f"var:{name}"

    if ds == "dataModelSlot":
        slot_id = iv.get("dataModelSlot")
        if slot_id:
            slot = ctx["dms_index"].get(slot_id)
            name = slot.get("name") if slot else slot_id
            return f"dms:{name}"

    if ds == "tableAggregation":
        tq_id  = iv.get("appTableQueryId")
        vs_id  = iv.get("tableAggregationVersionSetId")
        label = tq_id
        tq = ctx["tq_index"].get(tq_id) if tq_id else None

        if tq:
            agg_label = None
            for agg in tq.get("aggregations", []):
                if agg.get("aggregationVersionSetId") == vs_id:
                    agg_label = agg.get("label") or agg.get("aggregationId")
                    break
            if agg_label:
                label = agg_label
            else:
                label = tq.get("label") or tq_id

        return f"agg:{label}"

    if ds == "static" and "value" in iv:
        return f"static:{iv['value']}"

    expr = iv.get("exprStr") or iv.get("expression")
    if expr:
        # Don't embed JSON/expression details; just say "expr" 
        # //TODO Maybe change
        return "expr"

    return ds or "?"


def build_condition_label(cond: Dict[str, Any], ctx: Dict[str, Any]) -> str:
    c_type = cond.get("type") or cond.get("conditionType") or "condition"
    ivs = cond.get("input_values", []) or cond.get("inputs", [])

    refs = []
    for iv in ivs:
        desc = describe_input_value(iv, ctx)
        if desc:
            refs.append(desc)

    if refs:
        return f"{c_type}(" + ", ".join(refs) + ")"
    else:
        return c_type

# ---------- Mermaid data structure ----------

class Graph:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, str]] = {}   # id -> {"kind": "step"/"trigger"/"widget", "label": str}
        self.edges: List[Dict[str, str]] = []        # {"src": id, "dst": id, "kind": str, "label": str}

    def add_node(self, node_id: str, kind: str, label: str) -> None:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"kind": kind, "label": label}

    def add_edge(self, src: str, dst: str, kind: str, label: str = "") -> None:
        self.edges.append({"src": src, "dst": dst, "kind": kind, "label": label})


def build_graph_for_step(step: Dict,
                         trig_index: Dict[str, Dict],
                         widget_index: Dict[str, Dict],
                         all_steps_index: Dict[str, Dict]) -> Graph:
    g = Graph()

    step_id = step.get("_id")
    step_node_id = f"step_{step_id}"
    g.add_node(step_node_id, "step", step.get("name") or step_id)

    # 1) Step -> trigger edges
    for tid in step.get("triggers", []) or []:
        trig = trig_index.get(tid)
        if not trig:
            continue

        trig_node_id = f"trig_{tid}"
        label = (
            trig.get("description")
            or trig.get("label")
            or trig.get("name")
            or tid
        )
        g.add_node(trig_node_id, "trigger", label)

        etype = get_trigger_event_type(trig)
        g.add_edge(step_node_id, trig_node_id, kind="event", label=etype)

        # 2) Trigger -> Step transitions
        for clause in trig.get("clauses", []):
            for action in clause.get("actions", []):
                if not (action.get("is_transition") or action.get("isTransition")):
                    continue
                for iv in action.get("input_values", []) or []:
                    target = iv.get("stepId") or iv.get("step_id")
                    if not target:
                        continue
                    target_node_id = f"step_{target}"

                    target_step = all_steps_index.get(target)
                    target_label = (
                        target_step.get("name")
                        if target_step else target
                    )
                    g.add_node(target_node_id, "step", target_label)

                    g.add_edge(
                        trig_node_id,
                        target_node_id,
                        kind="transition",
                        label=action.get("type") or "transition",
                    )

    # 3) Widget -> trigger edges
    for wid in step.get("widgets", []) or []:
        w = widget_index.get(wid)
        if not w:
            continue

        trig_ids = get_widget_trigger_ids(w)
        if not trig_ids:
            continue

        wid_node_id = f"wid_{wid}"
        wlabel = get_widget_name(w)

        if is_button_widget(w):
            btext = get_button_text(w)
            if btext:
                wlabel = f"Button: {btext}"

        g.add_node(wid_node_id, "widget", wlabel)

        for tid in trig_ids:
            trig = trig_index.get(tid)
            if not trig:
                continue
            trig_node_id = f"trig_{tid}"
            tlabel = (
                trig.get("description")
                or trig.get("label")
                or trig.get("name")
                or tid
            )
            g.add_node(trig_node_id, "trigger", tlabel)
            g.add_edge(wid_node_id, trig_node_id, kind="widget_event", label="click")

    return g

# ---------- Mermaid rendering helpers ----------

def sanitize_label(text: str) -> str:
    """
    Make a Mermaid-safe label:
      - strip quotes, backslashes, and curly/square braces
      - collapse whitespace
      - truncate very long labels

    NOTE: we also strip the literal substring 'dms' so that "dms:Shift"
    turns into ":Shift" for a nicer data-model-slot notation.
    """
    if text is None:
        return ""
    text = str(text)

    # Remove characters that tend to break Mermaid parsing
    for ch in ['"', '\\', '{', '}', '[', ']','dms']:
        text = text.replace(ch, '')

    # Replace all whitespace runs (including newlines) with a single space
    text = " ".join(text.split())

    # Keep labels readable
    if len(text) > 120:
        text = text[:117] + "..."

    return text


def format_dm_action_label(atype: str, ivs: List[Dict[str, Any]], ctx: Dict[str, Any]) -> str:
    """
    Render load/unload/create-or-load data model actions in Tulip-ish language.
    Example:
      Load record :Shift from agg:ShiftID Day
    """
    slot_name = None
    source_desc = None

    for iv in ivs:
        ds = iv.get("datasourceType")
        if ds == "dataModelSlot" and not slot_name:
            slot_id = iv.get("dataModelSlot")
            slot = ctx["dms_index"].get(slot_id) if slot_id else None
            slot_name = slot.get("name") if slot else slot_id
        elif ds in ("tableAggregation", "variable", "static") and not source_desc:
            source_desc = describe_input_value(iv, ctx)

    # Friendlier description
    if atype == "load_data_model_record":
        verb = "Load record"
    elif atype == "unload_data_model_record":
        verb = "Unload record"
    elif atype == "create_or_load_data_model_record":
        verb = "Create or load record"
    else:
        verb = atype

    parts = [verb]
    if slot_name:
        parts.append(f":{slot_name}")
    if source_desc:
        parts.append(f"from {source_desc}")

    return " ".join(parts)


def emit_combined_mermaid(graph: Graph,
                          logic_triggers: Dict[str, Dict[str, Any]],
                          ctx: Dict[str, Any]) -> str:
    """
    Emit a single Mermaid flowchart that includes:
      - Step / widget / trigger graph for this step
      - For each trigger on this step, a nested subgraph showing its IF/THEN clauses
    """
    lines: List[str] = []
    lines.append("flowchart LR")

    # --- Core step/widgets/triggers graph ---

    for node_id, meta in graph.nodes.items():
        label = sanitize_label(meta["label"])
        kind = meta["kind"]

        if kind == "step":
            shape = f'["{label}"]'              # step = box
        elif kind == "trigger":
            shape = f'("Trig: {label}")'        # trigger = rounded
        else:  # widget
            shape = f'{{"Widget: {label}"}}'    # widget = curly

        lines.append(f"  {node_id}{shape}")

    for e in graph.edges:
        src = e["src"]
        dst = e["dst"]
        label = sanitize_label(e.get("label") or "")
        if label:
            lines.append(f'  {src} -->|{label}| {dst}')
        else:
            lines.append(f"  {src} --> {dst}")

    # --- Trigger logic subgraphs ---

    for tid, trig in logic_triggers.items():
        trig_id = trig.get("_id") or trig.get("id")
        if not trig_id:
            continue

        trig_label = (
            trig.get("description")
            or trig.get("label")
            or trig.get("name")
            or trig_id
        )
        base = f"trig_{trig_id}"
        safe_trig_label = sanitize_label(trig_label)

        lines.append(f'  subgraph {base}_logic["Logic: {safe_trig_label}"]')
        lines.append(f'    {base}_start(("start"))')

        clauses = trig.get("clauses", []) or []
        clause_nodes: List[Dict[str, Any]] = []

        for idx, clause in enumerate(clauses, start=1):
            cinfo: Dict[str, Any] = {
                "index": idx,
                "conditions": clause.get("conditions", []) or []
            }

            if cinfo["conditions"]:
                cond_node_id = f"{base}_c{idx}"
                label = build_condition_label(cinfo["conditions"][0], ctx)
                safe_label = sanitize_label(label)
                lines.append(f'    {cond_node_id}{{"{safe_label}"}}')
                cinfo["cond_id"] = cond_node_id

            actions = clause.get("actions", []) or []
            if actions:
                act_node_id = f"{base}_a{idx}"
                action_labels: List[str] = []
                for a in actions:
                    atype = a.get("type") or a.get("actionType") or "ACTION"
                    ivs   = a.get("input_values", []) or a.get("inputs", [])

                    # Use nicer labels for common data-model actions
                    if atype in (
                        "load_data_model_record",
                        "unload_data_model_record",
                        "create_or_load_data_model_record",
                    ):
                        label_str = format_dm_action_label(atype, ivs, ctx)
                    else:
                        refs  = [describe_input_value(iv, ctx) for iv in ivs]
                        ref_str = ", ".join(r for r in refs if r)
                        if ref_str:
                            label_str = f"{atype}({ref_str})"
                        else:
                            label_str = atype

                    action_labels.append(label_str)

                label = "; ".join(action_labels)
                safe_label = sanitize_label(label)
                lines.append(f'    {act_node_id}["{safe_label}"]')
                cinfo["act_id"] = act_node_id

            clause_nodes.append(cinfo)

        # Wire start → first clause / default
        if clause_nodes:
            first = clause_nodes[0]
            if "cond_id" in first:
                lines.append(f"    {base}_start --> {first['cond_id']}")
            else:
                if "act_id" in first:
                    lines.append(f"    {base}_start --> {first['act_id']}")

        # Wire clause chain (true/false fallthrough)
        for i, cinfo in enumerate(clause_nodes):
            cond_id = cinfo.get("cond_id")
            act_id  = cinfo.get("act_id")

            # TRUE path
            if cond_id and act_id:
                lines.append(f"    {cond_id} -->|true| {act_id}")

            # FALSE path to next clause/default
            if cond_id:
                next_clause = clause_nodes[i+1] if i+1 < len(clause_nodes) else None
                if next_clause:
                    if next_clause.get("cond_id"):
                        lines.append(f"    {cond_id} -->|false| {next_clause['cond_id']}")
                    elif next_clause.get("act_id"):
                        lines.append(f"    {cond_id} -->|false| {next_clause['act_id']}")

        lines.append("  end")

        # Connect the trigger node to its logic subgraph entry
        if base in graph.nodes:
            lines.append(f"  {base} --> {base}_start")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python inspect_step.py app.json [step_id]")
        sys.exit(1)

    json_file = sys.argv[1]

    # 1) Load JSON once
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 2) Load collections and build indexes
    table_queries = load_table_queries(data)
    variables     = load_variables(data)
    dm_slots      = load_data_model_slots(data)

    tq_index   = index_by_id(table_queries)
    var_index  = index_by_id(variables)
    dms_index  = index_by_id(dm_slots)

    steps    = load_steps(data)
    triggers = load_triggers(data)
    widgets  = load_widgets(data)

    steps_index  = index_by_id(steps)
    trig_index   = index_by_id(triggers)
    widget_index = index_by_id(widgets)

    resource_ctx = {
        "tq_index": tq_index,
        "var_index": var_index,
        "dms_index": dms_index,
    }

    print(f"Loaded {len(steps)} step(s).")

    # ---------- Step selection ----------
    step: Dict[str, Any] = None
    step_id: str = None

    if len(sys.argv) == 2:
        # Interactive mode: list steps, ask for a number
        print("\nAvailable steps:\n")
        sorted_steps = sorted(
            steps,
            key=lambda s: (
                (s.get("name") or "").lower(),
                (s.get("_id") or s.get("id") or "")
            ),
        )

        for idx, s in enumerate(sorted_steps, start=1):
            sid   = s.get("_id") or s.get("id") or "(no id)"
            name  = s.get("name") or "(no name)"
            proc  = s.get("parent_process") or ""
            group = s.get("parent_step_group") or ""
            extras = []
            if proc:
                extras.append(f"process={proc}")
            if group:
                extras.append(f"group={group}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            print(f"{idx:3d}. {name}  [id={sid}]{extra_str}")

        choice = input("\nEnter step number to inspect (or press Enter to quit): ").strip()
        if not choice:
            print("No selection made; exiting.")
            sys.exit(0)

        try:
            n = int(choice)
        except ValueError:
            print(f"'{choice}' is not a valid integer index.")
            sys.exit(1)

        if n < 1 or n > len(sorted_steps):
            print(f"Step number {n} is out of range (1-{len(sorted_steps)}).")
            sys.exit(1)

        step = sorted_steps[n - 1]
        step_id = step.get("_id") or step.get("id") or ""
        print(f"\nSelected step #{n}: {step.get('name') or '(no name)'} [id={step_id}]")

    else:
        step_id = sys.argv[2].strip()
        step = find_step(steps, step_id)
        if not step:
            print(f"Step with ID '{step_id}' not found in loaded steps.")

            sample_ids = [s.get("_id") or s.get("id") for s in steps[:10]]
            print("Here are some step IDs I *did* find (first up to 10):")
            for sid in sample_ids:
                print("  -", sid)

            print("\nDoing a quick substring search over step objects...")
            needle = step_id
            hits = []
            for s in steps:
                if needle in json.dumps(s):
                    hits.append(s.get("_id") or s.get("id"))
            if hits:
                print("Found the ID string inside these step objects:")
                for h in hits:
                    print("  -", h)
            else:
                print("Did not even find the ID string inside any step objects.")

            sys.exit(1)


    print("================ Step summary ================")
    print(f"Step ID: {step.get('_id')}")
    print(f"Name: {step.get('name')}")
    print(f"Parent process: {step.get('parent_process')}")
    print(f"Parent step group: {step.get('parent_step_group')}")
    print(f"Takt time: {step.get('takt_time')}")

    step_trigger_ids = step.get("triggers", []) or []
    step_widget_ids  = step.get("widgets", []) or []

    print(f"\nDirect trigger IDs ({len(step_trigger_ids)}): {step_trigger_ids}")
    print(f"Widget IDs ({len(step_widget_ids)}): {step_widget_ids}")

    # 5) Collect and categorize triggers (text view)
    step_trigger_objs = [trig_index.get(tid) for tid in step_trigger_ids]
    cats = categorize_step_triggers(step_trigger_objs)

    print("\n================ Step triggers (grouped) ================")
    print_trigger_category("On step enter",        cats["step_open"])
    print_trigger_category("Timers",               cats["interval"])
    print_trigger_category("Machines & devices",   cats["machines_output"])
    print_trigger_category("On step exit",         cats["step_closed"])
    print_trigger_category("Other",                cats["other"])

    # 6) Group widgets by type (only those with triggers)
    step_widget_objs = [widget_index.get(wid) for wid in step_widget_ids]
    step_widget_objs = [
        w for w in step_widget_objs
        if w is not None and get_widget_trigger_ids(w)
    ]
    grouped_widgets = group_widgets_by_type(step_widget_objs)
    print_widget_groups(grouped_widgets, trig_index)

    # 7) Collect triggers that appear on this step (direct + widget-attached)
    logic_triggers: Dict[str, Dict[str, Any]] = {}

    # Direct step triggers
    for trig in step_trigger_objs:
        if not trig:
            continue
        tid = trig.get("_id") or trig.get("id")
        if tid:
            logic_triggers[tid] = trig

    # Widget-attached triggers
    for w in step_widget_objs:
        for tid in get_widget_trigger_ids(w):
            t = trig_index.get(tid)
            if t:
                logic_triggers[tid] = t

    # 8) Build step graph + output combined Mermaid
    print("\n================ Mermaid (step + trigger logic) ================")
    if not logic_triggers and not step_trigger_ids and not step_widget_ids:
        print("  (no triggers or widgets on this step)")
    else:
        g = build_graph_for_step(step, trig_index, widget_index, steps_index)
        print(emit_combined_mermaid(g, logic_triggers, resource_ctx))


if __name__ == "__main__":
    main()
