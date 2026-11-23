"""HTML for the overlay and the custom-battle window (pure functions, no Qt)."""
import html


def _pct(x):
    return f"{100 * x:5.1f}%"


def render(state):
    """HTML for the overlay from the controller state dict."""
    esc = html.escape
    out = ['<div style="font-family:Segoe UI; font-size:15px; color:#eceff4;">']
    title = state.get("title") or "A&amp;A ODDS &middot; ATTACKER'S PERSPECTIVE (left side of the panel)"
    out.append(f'<div style="font-size:12px; color:#8fa1b3; letter-spacing:1px;">{title}</div>')
    if state.get("warnings"):
        out.append('<div style="color:#ffb454; margin-top:4px;">Could not read the whole panel:</div>')
        for w in state["warnings"][:5]:
            out.append(f'<div style="color:#ffb454; font-size:12px;">- {esc(w)}</div>')
        out.append('<div style="color:#8fa1b3; font-size:12px;">Teach the reader this screen '
                   '(see overlay/README.md).</div></div>')
        return "".join(out)
    r = state.get("result")
    out.append(f'<div style="margin-top:4px;"><b>Attacker</b> {esc(state["att_text"])} '
               f'<span style="color:#8fa1b3;">({state["att_ipc"]} IPC)</span></div>')
    out.append(f'<div><b>Defender</b> {esc(state["def_text"])} '
               f'<span style="color:#8fa1b3;">({state["def_ipc"]} IPC)</span></div>')
    if state.get("aa"):
        out.append(f'<div style="color:#8fa1b3; font-size:12px;">Defending AA guns (read from the panel): {state["aa"]}</div>')
    if r and r.get("error"):
        out.append(f'<div style="margin-top:8px; color:#ff7a6b;">Cannot calculate this battle: {esc(r["error"])}</div>'
                   '<div style="font-size:12px; color:#8fa1b3;">The panel may have been misread. '
                   'Ctrl+Alt+F8 opens the custom battle window to enter the units by hand.</div></div>')
        return "".join(out)
    if not r or not r.get("outcomes"):
        if r and r.get("stage") == "final":  # finished without a result (too large): say why
            for note in r.get("notes", []):
                out.append(f'<div style="margin-top:6px; font-size:12px; color:#ffb454;">{esc(note)}</div>')
        else:
            out.append('<div style="margin-top:8px; color:#ffd479;">Calculating...</div>')
        out.append("</div>")
        return "".join(out)
    o = r["outcomes"]
    badge = ("EXACT" if r["method"] == "exact" else
             f'ESTIMATE +/-{100 * (r["stderr"] or 0):.1f}% ({r["runs"]:,} runs)')
    colour = "#5ad07a" if r["method"] == "exact" else "#ffd479"
    out.append(f'<div style="margin-top:8px; font-size:12px; color:{colour};">{badge}</div>')
    # The headline is always the attacker's chance of winning (the attacker is the left side of the panel).
    out.append(f'<div style="margin-top:4px;"><span style="font-size:30px; color:#5ad07a;"><b>{100 * o["attacker_win"]:.1f}%</b></span>'
               ' <span style="font-size:14px; color:#8fa1b3;">Attacker wins</span></div>')
    rows = [("Defender wins", o["defender_win"], "#ff7a6b"), ("Mutual wipeout", o["draw"], "#c9b458")]
    if o["stalemate"] > 1e-9:
        rows.append(("Stalemate", o["stalemate"], "#8aa0b5"))
    for label, p in r.get("extra_rows") or []:
        rows.append((label, p, "#8aa0b5"))
    out.append('<table width="100%" cellspacing="0" cellpadding="1">')
    for label, p, col in rows:
        out.append(f'<tr><td>{label}</td><td align="right" style="color:{col};"><b>{_pct(p)}</b></td></tr>')
    out.append("</table>")
    segs = [(o["attacker_win"], "#5ad07a"), (o["draw"] + o["stalemate"], "#c9b458"), (o["defender_win"], "#ff7a6b")]
    out.append('<table width="100%" cellspacing="0" cellpadding="0"><tr>' + "".join(
        f'<td width="{max(1, round(100 * p))}%" bgcolor="{c}" height="8"></td>' for p, c in segs if p > 0.004) +
        "</tr></table>")
    if r.get("loss_att") is not None:
        out.append(f'<div style="margin-top:6px;">Expected IPC lost: attacker <b>{r["loss_att"]:.1f}</b>, '
                   f'defender <b>{r["loss_def"]:.1f}</b></div>')
    sp = r.get("spread")
    if sp:
        out.append('<div style="margin-top:6px; font-size:12px; color:#8fa1b3;">Casualty choice (attacker win %)</div>')
        out.append('<div style="font-size:13px;">'
                   f'standard {_pct(sp["standard"])} &middot; '
                   f'attacker optimal {_pct(sp["attacker_optimal"]) if sp["attacker_optimal"] is not None else "n/a"} &middot; '
                   f'defender optimal {_pct(sp["defender_optimal"]) if sp["defender_optimal"] is not None else "n/a"}</div>')
    for note in r.get("notes", []):
        out.append(f'<div style="margin-top:6px; font-size:12px; color:#ffb454;">{esc(note)}</div>')
    if r["stage"] == "final" or r["stage"] == "exact":
        out.append('<div style="margin-top:8px; font-size:11px; color:#6f7f90;">Assumptions: ' +
                   esc(" | ".join(r["assumptions"][:3])) + "</div>")
    out.append("</div>")
    return "".join(out)


def render_sbr(r, bombers, ipc_value, damage):
    """HTML for a strategic bombing raid result (``bombing.solve_sbr``)."""
    out = ['<div style="font-family:Segoe UI; font-size:15px; color:#eceff4;">',
           '<div style="font-size:12px; color:#8fa1b3; letter-spacing:1px;">STRATEGIC BOMBING RAID</div>',
           f'<div style="margin-top:4px;"><b>{bombers}</b> bomber(s) vs a complex in a <b>{ipc_value}</b>-IPC territory '
           f'(existing damage {damage}, cap {r["cap"]})</div>',
           f'<div style="margin-top:6px;"><span style="font-size:30px; color:#5ad07a;"><b>{r["expected_damage"]:.2f}</b></span>'
           ' <span style="font-size:14px; color:#8fa1b3;">expected damage (IPC)</span></div>',
           '<table width="100%" cellspacing="0" cellpadding="1">',
           f'<tr><td>Chance of no damage</td><td align="right"><b>{_pct(r["p_no_damage"])}</b></td></tr>',
           f'<tr><td>Chance the damage cap is reached</td><td align="right"><b>{_pct(r["p_cap_reached"])}</b></td></tr>',
           f'<tr><td>Expected bombers lost</td><td align="right"><b>{r["expected_bombers_lost"]:.2f}</b> '
           f'(~{r["expected_bomber_cost_lost"]:.1f} IPC)</td></tr>',
           '</table>',
           '<div style="margin-top:6px; font-size:12px; color:#8fa1b3;">Damage distribution</div>',
           '<table width="100%" cellspacing="0" cellpadding="0">']
    for dmg, p in r["damage_distribution"].items():
        if p >= 0.0005:
            out.append(f'<tr><td width="14%">{dmg}</td><td width="70%"><table width="{max(1, round(100 * p))}%" '
                       f'cellspacing="0" cellpadding="0"><tr><td bgcolor="#5aa0d0" height="8"></td></tr></table></td>'
                       f'<td align="right">{_pct(p)}</td></tr>')
    out.append("</table></div>")
    return "".join(out)
