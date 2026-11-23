"""Exact, compact materialized aggregates for small Git-history append updates.

All metrics are recomputed from sufficient statistics, not assumed unchanged.
The same-day, unchanged-configuration rule matters: a shifted time window
requires a full rebuild so historical commits can expire correctly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config
from .metrics.base import Context, fmt_pct, mean, median, monday_of, percentile
from .metrics.message import rule_violations
from .model import CheckResult, Commit, Evidence, FileChange, Finding, Severity
from .stats import change_pct

VERSION = 1


def pack(c: Commit) -> dict:
    return {"sha": c.sha, "author": c.author, "email": c.email,
            "date": c.date.isoformat(), "subject": c.subject, "body": c.body,
            "files": [[f.path, f.added, f.deleted, f.binary] for f in c.files]}


def unpack(c: dict) -> Commit:
    return Commit(c["sha"], c["author"], c["email"], datetime.fromisoformat(c["date"]),
                  c["subject"], c["body"], [FileChange(*item) for item in c["files"]])


def new_state(cfg: Config, now: datetime, author_labels: dict[str, str]) -> dict:
    return {"version": VERSION, "config_hash": cfg.fingerprint,
            "date": now.date().isoformat(), "author_labels": author_labels,
            "head": None,
            "count": 0, "first_ts": None, "last_ts": None,
            "first_date": None, "last_date": None, "authors": {},
            "weeks": {}, "pairs": {}, "files": {}, "samples": {},
            "message": {"offenders": 0, "rules": {}, "top": []}}


def include_new_pseudonymous_authors(state: dict, new_emails: set[str]) -> None:
    """Rekey aggregates when sorted Dev-N labels shift due to new identities.

    This avoids a full 500k-commit scan when an ordinary new contributor first
    appears, while matching labels obtained by a fresh full scan exactly.
    """
    old = dict(state["author_labels"])
    wanted = set(old) | set(new_emails)
    updated = {email: f"Dev-{i:02d}" for i, email in enumerate(sorted(wanted), 1)}
    if updated == old:
        return
    rename = {label: updated[email] for email, label in old.items()}
    state["authors"] = {rename[label]: value for label, value in state["authors"].items()}
    for file in state["files"].values():
        file["authors"] = {rename[label]: n for label, n in file["authors"].items()}
    for sample in state["samples"].values():
        sample["author"] = rename[sample["author"]]
    state["author_labels"] = updated


def add_commits(state: dict, commits: list[Commit], cfg: Config) -> None:
    """Update sufficient statistics with a batch containing only new commits."""
    rules = state["message"]["rules"]
    for c in commits:
        state["count"] += 1
        when = c.date.timestamp()
        if state["first_ts"] is None or when < state["first_ts"]:
            state["first_ts"] = when
            state["first_date"] = str(c.date.date())
        if state["last_ts"] is None or when > state["last_ts"]:
            state["last_ts"] = when
            state["last_date"] = str(c.date.date())
        a = state["authors"].setdefault(c.author, {"count": 0, "last_ts": None, "last_sha": "",
                                                  "last_date": ""})
        a["count"] += 1
        if a["last_ts"] is None or when >= a["last_ts"]:
            a["last_ts"] = when
            a["last_sha"] = c.sha
            a["last_date"] = c.date.isoformat()
            state["samples"][c.sha] = pack(c)

        week = monday_of(c.date.date()).isoformat()
        w = state["weeks"].setdefault(week, {"count": 0, "shas": []})
        w["count"] += 1
        w["shas"].append(c.sha)
        w["shas"] = w["shas"][-3:]
        state["samples"][c.sha] = pack(c)

        key = f"{c.nfiles}:{c.churn}"
        pair = state["pairs"].setdefault(key, {"count": 0, "shas": []})
        pair["count"] += 1
        if len(pair["shas"]) < 8:
            pair["shas"].append(c.sha)

        for f in c.files:
            fi = state["files"].setdefault(
                f.path, {"changes": 0, "churn": 0, "authors": {}, "shas": []})
            fi["changes"] += 1
            fi["churn"] += f.churn
            fi["authors"][c.author] = fi["authors"].get(c.author, 0) + 1
            fi["shas"].append(c.sha)
            fi["shas"] = fi["shas"][-8:]

        violations = rule_violations(c, cfg.get)
        if violations:
            state["message"]["offenders"] += 1
            for violation in violations:
                head = violation.split("（")[0]
                rules[head] = rules.get(head, 0) + 1
            top = state["message"]["top"]
            top.append({"sha": c.sha, "churn": c.churn, "violations": violations})
            top.sort(key=lambda x: x["churn"], reverse=True)
            del top[8:]

        if state["count"] % 10000 == 0:
            _prune_samples(state)

    _prune_samples(state)


def _prune_samples(state: dict) -> None:
    # The materialized state stores only SHAs needed for future diagnoses.
    needed = {info["last_sha"] for info in state["authors"].values()}
    needed.update(x["sha"] for x in state["message"]["top"])
    for group in (state["weeks"], state["pairs"], state["files"]):
        for item in group.values():
            needed.update(item["shas"])
    state["samples"] = {sha: data for sha, data in state["samples"].items() if sha in needed}


def save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode(), 3)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".rollup-", delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(payload)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, path)


def load_state(path: Path, cfg: Config, now: datetime) -> dict | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            value = json.load(f)
        if (value.get("version") != VERSION or value.get("config_hash") != cfg.fingerprint
                or value.get("date") != now.date().isoformat()):
            return None
        return value
    except (OSError, ValueError, KeyError):
        return None


def state_path(cache_metadata: Path) -> Path:
    return cache_metadata.with_suffix(".rollup.json.gz")


def _samples(state: dict, shas: list[str]) -> list[Commit]:
    return [unpack(state["samples"][sha]) for sha in shas if sha in state["samples"]]


def _weighted_percentile(counts: dict[int, int], q: float) -> float:
    if not counts:
        return 0.0
    count = sum(counts.values())
    pos = (count - 1) * q / 100
    lo, hi = int(pos), min(int(pos) + 1, count - 1)
    def value_at(idx: int) -> int:
        cursor = 0
        for value, n in sorted(counts.items()):
            cursor += n
            if idx < cursor:
                return value
        return max(counts)
    left, right = value_at(lo), value_at(hi)
    return left + (right - left) * (pos - lo)


def _empty(check: str, title: str, order: int) -> CheckResult:
    return CheckResult(check, title, Severity.INFO, "窗口内没有提交，无法评估", order=order)


def _format_commit(c: Commit, count: int = 12) -> Evidence:
    return Evidence(label=f"commit {c.short_sha}",
                    detail=f"{c.nfiles} 文件 / {c.churn} 行 · {c.author} · {c.subject}",
                    lines=[f"- {x}" for x in c.paths[:count]], commit=c.sha)


def _message_result(state: dict, cfg: Config) -> CheckResult:
    total = state["count"]
    if not total:
        return _empty("message", "提交信息规范", 1)
    from .metrics.message import _severity
    item = state["message"]
    offenders = item["offenders"]
    rules = Counter(item["rules"])
    ratio = offenders / total
    severity = _severity(ratio)
    findings = []
    if severity != Severity.OK:
        evidence = []
        for row in item["top"][:5]:
            c = unpack(state["samples"][row["sha"]])
            evidence.append(Evidence(label=f"commit {c.short_sha}",
                                     detail=f"「{c.subject}」改动 {c.nfiles} 个文件 / {c.churn} 行",
                                     lines=[f"- {v}" for v in row["violations"]], commit=c.sha))
        findings = [Finding("message", severity,
                            f"{offenders} 个提交的信息无法说明改了什么",
                            "消息格式和标题质量可能影响评审者理解，需人工结合改动上下文核对。",
                            "对不合规提交制定可执行的提交格式规范与针对性示例。", evidence)]
    labels, vals = zip(*rules.most_common(6)) if rules else (("无违规",), (0,))
    return CheckResult("message", "提交信息规范", severity,
                       f"{fmt_pct(ratio)} 的提交不符合规范（{offenders}/{total}）"
                       f"，最常见问题：{rules.most_common(1)[0][0] if rules else '无'}",
                       order=1, stats={"total": total, "offenders": offenders,
                                       "ratio": round(ratio, 4), "by_rule": dict(rules)},
                       chart={"type": "bar", "labels": list(labels), "values": list(vals), "unit": "次"},
                       findings=findings)


def _size_results(state: dict, cfg: Config) -> list[CheckResult]:
    total = state["count"]
    if not total:
        return [_empty("size", "单次改动规模", 2), _empty("giant", "巨型提交", 3)]
    file_hist, churn_hist = Counter(), Counter()
    records = []
    for pair, value in state["pairs"].items():
        nfile, churn = (int(x) for x in pair.split(":"))
        count = value["count"]
        records.append((nfile, churn, count, value["shas"]))
        file_hist[nfile] += count
        churn_hist[churn] += count
    median_files = _weighted_percentile(file_hist, 50)
    median_churn = _weighted_percentile(churn_hist, 50)
    p90_files = _weighted_percentile(file_hist, 90)
    p95_churn = _weighted_percentile(churn_hist, 95)
    max_files = cfg.get("size", "max_files")
    max_lines = cfg.get("size", "max_lines")
    rel_q = cfg.get("size", "giant_percentile")
    p_files = _weighted_percentile(file_hist, rel_q)
    p_lines = _weighted_percentile(churn_hist, rel_q)
    use_relative = total >= 20 and 0 < rel_q < 100
    giant, wide = [], []
    total_churn = sum(n * value for value, n in churn_hist.items()) or 1.0
    giant_count = wide_count = giant_churn = 0
    for nfile, churn, count, shas in records:
        if nfile > max_files:
            wide.append((nfile, churn, shas))
            wide_count += count
        is_outlier = use_relative and (
            (nfile > p_files and nfile >= max(4, 3 * median_files))
            or (churn > p_lines and churn >= max(100, 3 * median_churn)))
        if nfile >= max_files or churn >= max_lines or is_outlier:
            giant.append((nfile, churn, shas))
            giant_count += count
            giant_churn += churn * count
    giant_share = giant_churn / total_churn
    wide_samples = sorted((_samples(state, shas)[0] for _, _, shas in wide if shas),
                          key=lambda x: x.nfiles, reverse=True)[:cfg.get("size", "top_n")]
    giant_samples = sorted((c for _, _, shas in giant for c in _samples(state, shas)),
                           key=lambda x: x.churn, reverse=True)[:cfg.get("size", "top_n")]
    size_sev = Severity.WARN if wide_count / total >= .1 else Severity.INFO if wide_count else Severity.OK
    giant_sev = (Severity.ALERT if giant_share >= .3 else Severity.WARN if giant_share >= .15
                 else Severity.INFO if giant_count else Severity.OK)
    size_findings = ([Finding("size", size_sev, f"{wide_count} 个提交修改文件数超过阈值",
                              "文件数属于变更规模代理，不能直接证明质量风险。",
                              "人工检查变更目的和拆分可能性。",
                              [_format_commit(c) for c in wide_samples])]
                     if wide_count else [])
    giant_findings = ([Finding("giant", giant_sev, f"{giant_count} 个大规模提交",
                               "规模阈值和相对极端离群同时检查，不能认定多个关注点被混合。",
                               "审阅原始 diff，必要时分拆提交。",
                               [_format_commit(c) for c in giant_samples])]
                      if giant_count else [])
    buckets = [sum(n for v, n in file_hist.items() if fn(v)) for fn in (
        lambda v: v == 1, lambda v: 2 <= v <= 3, lambda v: 4 <= v <= 9,
        lambda v: 10 <= v <= 29, lambda v: 30 <= v <= 99, lambda v: v >= 100)]
    size = CheckResult("size", "单次改动规模", size_sev,
                       f"提交改动中位数 {median_files:.0f} 个文件、{median_churn:.0f} 行；"
                       f"P90 为 {p90_files:.0f} 个文件，{wide_count} 个提交改了超过 {max_files} 个文件",
                       order=2, stats={"median_files": median_files, "p90_files": p90_files,
                                       "median_churn": median_churn, "p95_churn": p95_churn,
                                       "over_max_files": wide_count, "max_files": max_files},
                       chart={"type": "bar", "labels": ["1 个文件", "2-3 个", "4-9 个", "10-29 个", "30-99 个", "100+ 个"],
                              "values": buckets, "unit": "个提交"}, findings=size_findings)
    giant_result = CheckResult("giant", "巨型提交", giant_sev,
                               f"{giant_count} 个规模异常提交（≥ {max_files} 文件、≥ {max_lines} 行"
                               f"或 P{rel_q:g} 极端离群），占 {fmt_pct(giant_count / total)} 的提交数、"
                               f"却吃掉 {fmt_pct(giant_share)} 的代码改动量",
                               order=3, stats={"count": giant_count, "ratio": round(giant_count / total, 4),
                                               "churn_share": round(giant_share, 4), "max_files": max_files,
                                               "max_lines": max_lines, "giant_percentile": rel_q,
                                               "percentile_files_threshold": round(p_files, 2),
                                               "percentile_churn_threshold": round(p_lines, 2),
                                               "relative_enabled": use_relative},
                               chart={"type": "bar", "labels": [c.short_sha for c in giant_samples[:8]] or ["无"],
                                      "values": [c.churn for c in giant_samples[:8]] or [0], "unit": "行改动"},
                               findings=giant_findings)
    return [size, giant_result]


def _cadence_results(state: dict, cfg: Config, now: datetime) -> list[CheckResult]:
    if not state["count"]:
        return [_empty("cadence_freq", "提交频率异常", 4), _empty("cadence_dormant", "长期未提交", 5)]
    authors = state["authors"]
    weeks = max(((date.fromisoformat(state["last_date"]) -
                  date.fromisoformat(state["first_date"])).days + 1) / 7, 1.0)
    counts = {a: v["count"] for a, v in authors.items()}
    rates = {a: count / weeks for a, count in counts.items()}
    n_people = len(authors)
    multiplier = cfg.get("cadence", "freq_multiplier")
    min_count = cfg.get("cadence", "min_commits_for_freq")
    others_median = lambda a: median([v for name, v in rates.items() if name != a])
    frequent = [(a, rates[a], counts[a]) for a in rates if n_people >= 2
                and counts[a] >= min_count and rates[a] >= 1
                and rates[a] > others_median(a) * multiplier]
    frequent.sort(key=lambda x: x[1], reverse=True)
    f_sev = Severity.WARN if len(frequent) >= 3 else Severity.INFO if frequent else Severity.OK
    f_findings = []
    if frequent:
        f_findings = [Finding("cadence_freq", f_sev,
                              f"{len(frequent)} 人提交频率相对偏高",
                              "仅用于观察提交节奏，不用于衡量个人绩效。",
                              "核查提交粒度与工作分工。",
                              [Evidence(a, f"{count} 次 / {weeks:.1f} 周 = {rate:.2f} 次/周，"
                                        f"其他人中位数 {others_median(a):.2f}",
                                        commit=authors[a]["last_sha"])
                               for a, rate, count in frequent[:5]])]
    top_authors = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    freq = CheckResult("cadence_freq", "提交频率异常", f_sev,
                       f"窗口内 {state['count']} 次提交、{n_people} 人参与，"
                       f"人均 {state['count'] / n_people:.1f} 次；"
                       + (f"{len(frequent)} 人提交频率显著高于其他人" if frequent
                          else "没有人的提交频率明显偏离团队"),
                       order=4, stats={"weeks": round(weeks, 1),
                                       "median_per_week": round(median(list(rates.values())), 2),
                                       "frequent": [a for a, _, _ in frequent]},
                       chart={"type": "bar", "labels": [a for a, _ in top_authors],
                              "values": [v for _, v in top_authors], "unit": "次"},
                       findings=f_findings)
    day_limit = cfg.get("cadence", "dormant_days")
    min_dormant = cfg.get("cadence", "min_commits_for_dormant")
    dormant = []
    for a, info in authors.items():
        gap = max((now - datetime.fromisoformat(info["last_date"])).days, 0)
        if gap >= day_limit and info["count"] >= min_dormant:
            dormant.append((a, gap, info["count"], info["last_date"], info["last_sha"]))
    dormant.sort(key=lambda x: x[1], reverse=True)
    ratio = len(dormant) / n_people
    d_sev = Severity.ALERT if ratio >= .5 else Severity.WARN if dormant else Severity.OK
    d_findings = ([Finding("cadence_dormant", d_sev,
                           f"{len(dormant)} 人已连续 {day_limit} 天以上没有提交",
                           "可能是按计划收尾或出现阻塞，不能直接判定人员流失。",
                           "与团队 backlog 和成员实际计划核对。",
                           [Evidence(a, f"最后提交 {last}（{gap} 天前），此前 {count} 次",
                                     commit=sha)
                            for a, gap, count, last, sha in dormant[:6]])]
                  if dormant else [])
    dorm = CheckResult("cadence_dormant", "长期未提交", d_sev,
                       (f"{len(dormant)} 人已连续 ≥{day_limit} 天没有提交"
                        f"（{'、'.join(x[0] for x in dormant[:3])}），"
                        f"占 {fmt_pct(ratio)} 的贡献者" if dormant else
                        f"没有成员超过 {day_limit} 天未提交"),
                       order=5, stats={"dormant": [x[0] for x in dormant],
                                       "threshold_days": day_limit},
                       chart={"type": "bar", "labels": [x[0] for x in dormant] or ["无"],
                              "values": [x[1] for x in dormant] or [0], "unit": "天未提交"},
                       findings=d_findings)
    return [freq, dorm]


def _file_evidence(state: dict, path: str, detail: str, extras: list[str]) -> Evidence:
    file = state["files"][path]
    return Evidence(path, detail,
                    extras + [f"- 关联提交 SHA：{sha}" for sha in file["shas"]],
                    commit=file["shas"][-1] if file["shas"] else None)


def _file_results(state: dict, cfg: Config) -> list[CheckResult]:
    if not state["count"]:
        return [_empty("hotspot", "热点文件", 6), _empty("ownership", "知识集中度", 7)]
    files = state["files"]
    ranked = sorted(files.items(), key=lambda kv: (kv[1]["changes"], kv[1]["churn"]), reverse=True)
    total_changes = sum(s["changes"] for s in files.values()) or 1
    top = [(p, s) for p, s in ranked if s["changes"] >= cfg.get("hotspot", "min_changes")]
    top = top[:cfg.get("hotspot", "top_n")]
    top_share = sum(s["changes"] for _, s in top) / total_changes
    hottest_path, hottest = ranked[0] if ranked else ("-", {"changes": 0, "authors": {}})
    h_sev = (Severity.WARN if hottest["changes"] >= cfg.get("hotspot", "min_changes")
             and hottest["changes"] / total_changes >= .4 else Severity.INFO)
    h_findings = ([Finding("hotspot", h_sev, f"{len(top)} 个文件承担了 {fmt_pct(top_share)} 的改动",
                           "热点代表修改次数较集中，但不直接说明缺陷多。",
                           "为频繁修改的文件补测试并评审职责边界。",
                           [_file_evidence(state, p,
                                           f"被改 {s['changes']} 次 / {s['churn']} 行 / {len(s['authors'])} 人",
                                           [f"- 参与人：{'、'.join(sorted(s['authors']))}"])
                            for p, s in top[:5]])] if top else [])
    hotspot = CheckResult("hotspot", "热点文件", h_sev,
                          f"共 {len(files)} 个文件被改动；最热的 {len(top)} 个文件承担了 "
                          f"{fmt_pct(top_share)} 的改动，其中 `{hottest_path}` 被改"
                          f" {hottest['changes']} 次（{len(hottest['authors'])} 人参与）",
                          order=6, stats={"n_files": len(files), "top_share": round(top_share, 4),
                                          "top": [{"path": p, "changes": s["changes"],
                                                   "authors": len(s["authors"])} for p, s in top]},
                          chart={"type": "table", "columns": ["文件", "改动次数", "改动行数", "参与人数"],
                                 "rows": [[f"`{p}`", str(s["changes"]), f"{s['churn']:,}",
                                           str(len(s["authors"]))] for p, s in top]},
                          findings=h_findings)

    min_changes = cfg.get("ownership", "min_changes")
    share_threshold = cfg.get("ownership", "single_owner_share")
    coverage = cfg.get("ownership", "bus_factor_coverage")
    shared_min = cfg.get("ownership", "shared_min_authors")
    eligible = {p: f for p, f in files.items() if f["changes"] >= min_changes}
    singles, shared = [], []
    for path, f in eligible.items():
        names = Counter(f["authors"])
        top_author, top_n = names.most_common(1)[0]
        share = top_n / f["changes"]
        if share >= share_threshold:
            singles.append((path, f, share))
        elif len(names) >= shared_min:
            shared.append((path, f))
    singles.sort(key=lambda x: (x[1]["changes"], x[2]), reverse=True)
    shared.sort(key=lambda x: len(x[1]["authors"]), reverse=True)
    author_totals = Counter()
    for f in files.values():
        author_totals.update(f["authors"])
    grand = sum(author_totals.values())
    cum, bf, detail = 0, 0, []
    for name, count in author_totals.most_common():
        cum += count
        bf += 1
        detail.append((name, round(cum / grand, 3)))
        if cum / grand >= coverage:
            break
    ratio = len(singles) / max(len(eligible), 1)
    o_sev = (Severity.ALERT if ratio >= .30 else Severity.WARN if len(singles) >= 3
             else Severity.INFO if singles else Severity.OK)
    o_findings = []
    if singles:
        o_findings.append(Finding("ownership", o_sev,
                                  f"{len(singles)} 个文件的修改活动集中于同一人（活动代理 {bf}）",
                                  "改动占比只是活动代理，不代表知识实际覆盖。",
                                  "对高集中度文件核对交接文档与第二位维护者。",
                                  [_file_evidence(state, p,
                                                  f"{share:.0%} 的修改来自 {Counter(f['authors']).most_common(1)[0][0]}",
                                                  [f"- {a}: {n} 次" for a, n in Counter(f["authors"]).most_common(6)])
                                   for p, f, share in singles[:5]]))
    if shared:
        o_findings.append(Finding("ownership", Severity.INFO,
                                  f"{len(shared)} 个文件被 {shared_min} 人以上反复修改",
                                  "跨人员修改密集，可能需要协调接口。", "复核职责划分。",
                                  [_file_evidence(state, p, f"{f['changes']} 次改动，{len(f['authors'])} 人",
                                                  [f"- {a}: {n} 次" for a, n in Counter(f["authors"]).most_common(6)])
                                   for p, f in shared[:5]]))
    ownership = CheckResult("ownership", "知识集中度", o_sev,
                            f"在 {len(eligible)} 个被改动 ≥{min_changes} 次的文件中，"
                            f"{len(singles)} 个（{fmt_pct(ratio)}）有 {share_threshold:.0%} 以上的"
                            f"改动来自同一人；贡献活动集中度代理 = {bf} 人"
                            f"（{bf} 人累计覆盖 {coverage:.0%} 的历史文件修改次数，不能据此推断真实知识覆盖）",
                            order=7, stats={"eligible_files": len(eligible),
                                            "single_owner_files": len(singles), "ratio": round(ratio, 4),
                                            "bus_factor": bf, "bus_factor_cumulative": detail,
                                            "bus_factor_interpretation": "historical file-change concentration proxy; not verified knowledge or expertise",
                                            "shared_files": [p for p, _ in shared[:10]]},
                            chart={"type": "table", "columns": ["文件", "主导者", "占比", "改动次数", "参与人数"],
                                   "rows": [[f"`{p}`", Counter(f["authors"]).most_common(1)[0][0],
                                             f"{share:.0%}", str(f["changes"]), str(len(f["authors"]))]
                                            for p, f, share in singles[:cfg.get("ownership", "top_n")]]},
                            findings=o_findings)
    return [hotspot, ownership]


def _trend_result(state: dict, cfg: Config, now: datetime) -> CheckResult:
    """Reuse the exact tested trend algorithm with the materialized weekly series."""
    from .metrics.trend import run as trend_run
    sample_shas = [sha for w in state["weeks"].values() for sha in w["shas"]]
    commits = sorted(_samples(state, sample_shas), key=lambda c: c.date)
    ctx = Context(commits=commits, config=cfg, repo="<incremental>", now=now)
    counts = {date.fromisoformat(d): value["count"] for d, value in state["weeks"].items()}
    if not counts:
        return _empty("trend", "活跃度趋势", 8)
    first = min(counts)
    end = monday_of(now.date())
    values = []
    from datetime import timedelta
    cur = first
    while cur <= end:
        values.append((cur, counts.get(cur, 0)))
        cur += timedelta(days=7)
    ctx.weekly = lambda weeks=None: values[-weeks:] if weeks else values
    return trend_run(ctx)[0]


def run_from_state(state: dict, cfg: Config, now: datetime) -> list[CheckResult]:
    results = [_message_result(state, cfg)]
    results.extend(_size_results(state, cfg))
    results.extend(_cadence_results(state, cfg, now))
    results.extend(_file_results(state, cfg))
    results.append(_trend_result(state, cfg, now))
    results.sort(key=lambda r: r.order)
    return results


def make_context(state: dict, cfg: Config, now: datetime, repo: str) -> Context:
    """Provide report HTML with only the cited commits, not the whole history."""
    samples = sorted((unpack(c) for c in state["samples"].values()), key=lambda c: c.date)
    ctx = Context(samples, cfg, repo, now)
    # Additional explicit summary fields prevent counting sampled commits as
    # though they were the full history.
    ctx.total_commits = state["count"]
    ctx.total_authors = list(state["authors"])
    ctx.first_last = ((date.fromisoformat(state["first_date"]), date.fromisoformat(state["last_date"]))
                      if state["count"] else None)
    return ctx
