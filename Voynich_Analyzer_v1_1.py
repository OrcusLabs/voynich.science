import json
import re
import argparse
import io
import sys
import threading
import traceback
import tkinter as tk
from contextlib import redirect_stdout
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

MAPPINGS_FILE = "mappings_v3.json"
GALLOWS = set("ktpf")

def strip_gallows(token):
    return "".join(c for c in token if c not in GALLOWS)

def edit_distance(a, b, max_distance=2):
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            val = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
            curr.append(val)
            row_min = min(row_min, val)
        if row_min > max_distance:
            return max_distance + 1
        prev = curr
    return prev[-1]

def load_words(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["words"]

def normalize_folio(value):
    folio = value.strip()
    if not folio:
        return folio
    return folio if folio.startswith("f") else f"f{folio}"

def folio_sort_key(folio):
    match = re.match(r"^f?(\d+)([rv])?([A-Za-z0-9]*)$", str(folio))
    if not match:
        return (float("inf"), str(folio), 0, "")
    number, side, suffix = match.groups()
    side_order = {"r": 0, "v": 1}.get(side, 2)
    suffix_match = re.match(r"^(\d+)$", suffix or "")
    suffix_key = (0, int(suffix)) if suffix_match else (1, suffix or "")
    return (int(number), side_order, suffix_key, str(folio))

def format_counter(values):
    clean = [str(v) for v in values if v not in (None, "")]
    if not clean:
        return "unknown"
    counts = Counter(clean)
    return ", ".join(f"{value} ({count})" for value, count in counts.most_common())

def occurrence_context(occ):
    return {
        "folio": occ.get("folio"),
        "line": occ.get("line", "0"),
        "pos": int(occ.get("position", 0)),
        "quire": occ.get("quire"),
        "sheet": occ.get("sheet"),
        "currier_language": occ.get("currier_language"),
        "hands": occ.get("hands", []),
        "scribes": occ.get("scribes", []),
    }

def line_sort_key(value):
    match = re.match(r"^(\d+)(.*)$", str(value))
    if not match:
        return (0, str(value))
    return (int(match.group(1)), match.group(2))

def flatten_context_values(records, field):
    values = []
    for rec in records:
        value = rec.get(field)
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values

def collect_folio_context(words, target_page):
    contexts = []
    for info in words.values():
        for occ in info["occurrences"]:
            if occ["folio"] == target_page:
                contexts.append(occurrence_context(occ))
    return contexts

def collect_all_folios(words):
    folios = set()
    for info in words.values():
        for occ in info["occurrences"]:
            folios.add(occ["folio"])
    return sorted(folios, key=folio_sort_key)

def folio_has_scribe(words, folio, scribe):
    for info in words.values():
        for occ in info["occurrences"]:
            if occ["folio"] == folio and scribe in occ.get("scribes", []):
                return True
    return False

def collect_page_instances(words, target_page):
    instances = []
    for token, info in words.items():
        for occ in info["occurrences"]:
            if occ["folio"] == target_page and len(token) >= 3:
                rec = occurrence_context(occ)
                rec.update({
                    "pos": int(occ["position"]),
                    "token": token,
                    "stripped": strip_gallows(token),
                })
                instances.append(rec)
    return sorted(instances, key=lambda r: (line_sort_key(r["line"]), r["pos"]))

def unique_first_occurrences(instances):
    seen = set()
    unique = []
    for rec in instances:
        if rec["token"] in seen:
            continue
        seen.add(rec["token"])
        unique.append(rec)
    return unique

def remove_same_page_copy_ed1(instances):
    """
    Remove same-page local productions from the external-source core.

    A token instance is considered locally derived if an earlier token
    on the same page is either:

      ED0 = exact same stripped form
      ED1 = one-edit-distance stripped form

    This operates on ALL instances, not only unique token types.
    """
    earlier = []
    derived = []
    core = []

    for rec in instances:
        best_parent = None
        best_d = None

        for prev in earlier:
            d = edit_distance(rec["stripped"], prev["stripped"], 1)

            if d > 1:
                continue

            if best_parent is None:
                best_parent = prev
                best_d = d
                continue

            # Prefer exact copy over ED1.
            # If tied, prefer the nearest earlier occurrence.
            if d < best_d:
                best_parent = prev
                best_d = d

        if best_parent is not None:
            derived.append((rec, best_parent))
        else:
            core.append(rec)

        earlier.append(rec)

    return core, derived

def build_candidates(words, target_page, scribe_filter=None):
    """
    Build prior-folio candidates.

    Deduplicates to ONE earliest occurrence per:
        (stripped_form, folio)

    This prevents common words from flooding the candidate pool with
    dozens of duplicate same-folio occurrences.
    """
    target_key = folio_sort_key(target_page)
    best = {}

    for token, info in words.items():
        for occ in info["occurrences"]:
            folio = occ["folio"]
            
            # --- Scribe filter ---
            if scribe_filter is not None:
                if scribe_filter not in occ.get("scribes", []):
                    continue
                
            if folio == target_page:
                continue

            if folio_sort_key(folio) >= target_key:
                continue

            rec = occurrence_context(occ)
            rec.update({
                "token": token,
                "stripped": strip_gallows(token),
            })

            key = (rec["stripped"], folio)

            existing = best.get(key)
            rec_pos_key = (line_sort_key(rec["line"]), rec["pos"])

            if existing is None:
                best[key] = rec
            else:
                existing_pos_key = (line_sort_key(existing["line"]), existing["pos"])
                if rec_pos_key < existing_pos_key:
                    best[key] = rec

    return list(best.values())

def collect_prior_matches(core, candidates):
    """
    Multi-parent best-distance version.

    For each target instance:
      - find best edit distance among prior candidates
      - keep all tied best-distance matches
      - deduplicate hits to ONE match per source folio
    """
    matches_by_target = {}

    for rec in core:
        target_id = (rec["folio"], rec["line"], rec["pos"], rec["token"])

        # Safer ED2 rule:
        # only allow ED2 on longer stripped forms.
        max_d = 2 if len(rec["stripped"]) >= 6 else 1

        best_d = max_d + 1
        hits = []

        for cand in candidates:
            d = edit_distance(rec["stripped"], cand["stripped"], max_d)

            if d > max_d:
                continue

            if d < best_d:
                best_d = d
                hits = []

            if d == best_d:
                hits.append({
                    "target_id": target_id,
                    "target": rec,
                    "source": cand,
                    "distance": d,
                })

        if hits:
            # Keep one best representative per source folio.
            # Since all hits here are already tied at best_d,
            # choose earliest line/position within each folio.
            best_by_folio = {}

            for hit in hits:
                src = hit["source"]
                src_folio = src["folio"]
                src_key = (
                    line_sort_key(src["line"]),
                    src["pos"],
                    src["token"],
                )

                existing = best_by_folio.get(src_folio)

                if existing is None:
                    best_by_folio[src_folio] = hit
                else:
                    ex_src = existing["source"]
                    ex_key = (
                        line_sort_key(ex_src["line"]),
                        ex_src["pos"],
                        ex_src["token"],
                    )

                    if src_key < ex_key:
                        best_by_folio[src_folio] = hit

            matches_by_target[target_id] = list(best_by_folio.values())

    return matches_by_target

def build_sheet_targets(matches_by_target):
    """
    Build sheet coverage over target INSTANCES, not token strings.

    A sheet covers a target instance if that sheet contains at least one
    best-distance prior match for that instance.
    """
    sheet_targets = defaultdict(set)
    sheet_matches = defaultdict(lambda: defaultdict(list))

    for target_id, matches in matches_by_target.items():
        for match in matches:
            source = match["source"]
            sheet = (source.get("quire"), source.get("sheet"))

            sheet_targets[sheet].add(target_id)
            sheet_matches[sheet][target_id].append(match)

    return sheet_targets, sheet_matches

def solve_sheet_cover(sheet_targets, coverable_tokens, max_sheets, greedy_limit=None):
    """
    Solve source-sheet cover.

    Phase 1:
      Try exact full cover up to max_sheets.

    Phase 2:
      If exact full cover fails, run capped greedy best-effort.
      This prevents adding junk sheets just to cover one leftover token.
    """
    coverable = set(coverable_tokens)

    if not coverable:
        return [], set(), True

    sheets = sorted(
        sheet_targets,
        key=lambda s: (
            -len(sheet_targets[s] & coverable),
            source_sort_value(s[0]),
            source_sort_value(s[1]),
        ),
    )

    upper = min(max_sheets, len(sheets))

    # Phase 1: exact full-cover search.
    for k in range(1, upper + 1):
        for combo in combinations(sheets, k):
            covered = set()

            for sheet in combo:
                covered.update(sheet_targets[sheet])

            covered &= coverable

            if covered >= coverable:
                return list(combo), covered, True

    # Phase 2: capped greedy best-effort.
    if greedy_limit is None:
        greedy_limit = max_sheets

    selected = []
    covered = set()
    remaining_sheets = list(sheets)

    for _ in range(min(greedy_limit, len(remaining_sheets))):
        best_sheet = None
        best_gain = set()
        best_key = None

        for sheet in remaining_sheets:
            sheet_cover = sheet_targets[sheet] & coverable
            gain = sheet_cover - covered

            if not gain:
                continue

            sheet_key = (
                len(gain),          # most new coverage
                len(sheet_cover),   # then most total coverage
                source_sort_value(sheet[0]),
                source_sort_value(sheet[1]),
            )

            if best_sheet is None or sheet_key > best_key:
                best_sheet = sheet
                best_gain = gain
                best_key = sheet_key

        if best_sheet is None:
            break

        selected.append(best_sheet)
        covered.update(best_gain)
        remaining_sheets.remove(best_sheet)

    return selected, covered, False

def choose_assigned_match(target_id, selected_sheets, sheet_matches):
    """
    Choose the best displayed match for one target instance from the selected sheets.
    """
    best = None

    for sheet in selected_sheets:
        for match in sheet_matches.get(sheet, {}).get(target_id, []):
            if best is None:
                best = match
                continue

            best_key = (
                best["distance"],
                folio_sort_key(best["source"]["folio"]),
                line_sort_key(best["source"]["line"]),
                best["source"]["pos"],
                best["source"]["token"],
            )

            match_key = (
                match["distance"],
                folio_sort_key(match["source"]["folio"]),
                line_sort_key(match["source"]["line"]),
                match["source"]["pos"],
                match["source"]["token"],
            )

            if match_key < best_key:
                best = match

    return best

def source_sort_value(value):
    return (value is None, value)

def print_folio_summary(target_page, contexts, total, unique, core, derived):
    print(f"FOLIO: {target_page}")
    if contexts:
        print(f"QUIRE(S): {format_counter(flatten_context_values(contexts, 'quire'))}")
        print(f"SHEET(S): {format_counter(flatten_context_values(contexts, 'sheet'))}")
        print(f"SCRIBE(S): {format_counter(flatten_context_values(contexts, 'scribes'))}")
        print(f"HAND(S): {format_counter(flatten_context_values(contexts, 'hands'))}")
        print(f"CURRIER LANGUAGE(S): {format_counter(flatten_context_values(contexts, 'currier_language'))}")
    print(f"TOTAL WORD INSTANCES LEN>=3: {total}")
    print(f"UNIQUE TOKENS LEN>=3: {len(unique)}")
    print(f"CORE TOKENS TESTED: {len(core)}")
    print(f"SAME-FOLIO ED1 DERIVED TOKENS: {len(derived)}")

def print_cover_summary(selected_sheets, covered_tokens, coverable_tokens, core, sheet_targets, exact_cover, max_sheets):
    coverable = set(coverable_tokens)
    core_ids = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"])
        for rec in core
    }

    covered = set(covered_tokens) & coverable
    uncovered = coverable - covered

    print()
    print("SOURCE-SHEET COVER")
    print(f"CORE TOKENS WITH AT LEAST ONE PRIOR-FOLIO MATCH: {len(coverable)}")

    if exact_cover:
        print(f"EXACT SMALLEST SHEET COUNT FOUND: {len(selected_sheets)}")
        print(f"COVER METHOD: exact full cover within max_sheets={max_sheets}")
    else:
        print(f"BEST-EFFORT SHEET SET FOUND: {len(selected_sheets)}")
        print(f"COVER METHOD: no exact full cover within max_sheets={max_sheets}; capped greedy result shown")

    print("SELECTED SHEETS:")

    if not selected_sheets:
        print("  none")

    running = set()

    for sheet in selected_sheets:
        sheet_cover = sheet_targets[sheet] & coverable
        new_cover = sheet_cover - running
        running.update(sheet_cover)

        print(
            f"  quire {sheet[0]}, sheet {sheet[1]}: "
            f"covers {len(sheet_cover)} core tokens; adds {len(new_cover)}"
        )

    print(f"COVERED CORE TOKENS / COVERABLE CORE TOKENS: {len(covered)} / {len(coverable)}")
    print(f"COVERED CORE TOKENS / TOTAL CORE TOKENS: {len(covered)} / {len(core_ids)}")

    print(f"UNCOVERED COVERABLE CORE TOKENS: {len(uncovered)}")

    if uncovered:
        uncovered_tokens = sorted({target_id[3] for target_id in uncovered})
        shown = ", ".join(uncovered_tokens[:30])
        suffix = " ..." if len(uncovered_tokens) > 30 else ""
        print(f"  {shown}{suffix}")

def summarize_group(matches, key_func):
    groups = defaultdict(list)
    for match in matches:
        groups[key_func(match["source"])].append(match)
    return groups

def ordered_groups(groups):
    return sorted(
        groups.items(),
        key=lambda item: (-len(item[1]), tuple(source_sort_value(v) for v in item[0]))
    )

def print_top_cluster_summary(sheet_groups, folio_groups):
    print()
    print("SOURCE CLUSTER SUMMARY")
    print(f"  matched source sheets: {len(sheet_groups)}")
    for key, matches in ordered_groups(sheet_groups)[:10]:
        print(f"    quire {key[0]}, sheet {key[1]}: {len(matches)} matches")
    print(f"  matched source folios: {len(folio_groups)}")
    for key, matches in ordered_groups(folio_groups)[:15]:
        print(f"    {key[0]} (quire {key[1]}, sheet {key[2]}): {len(matches)} matches")

def print_cluster(title, groups, formatter):
    print()
    print(title)
    if not groups:
        print("  none")
        return
    for key, matches in ordered_groups(groups):
        sources = [m["source"] for m in matches]
        distances = Counter(m["distance"] for m in matches)
        tokens = Counter(m["source"]["token"] for m in matches)
        print(f"  {formatter(key)}")
        print(f"    matches: {len(matches)} | exact: {distances.get(0, 0)} | ed1: {distances.get(1, 0)} | ed2: {distances.get(2, 0)}")
        print(f"    folios: {format_counter(flatten_context_values(sources, 'folio'))}")
        print(f"    scribes: {format_counter(flatten_context_values(sources, 'scribes'))}")
        print(f"    hands: {format_counter(flatten_context_values(sources, 'hands'))}")
        print(f"    top source tokens: {', '.join(token for token, _ in tokens.most_common(8))}")

def print_core_assignments(core, matches_by_target, selected_sheets, sheet_matches):
    """
    Print assignments by target instance.

    This answers:
        which external sheet, if any, explains this actual word occurrence?
    """
    print()
    print("CORE INSTANCE ASSIGNMENTS")

    for rec in core:
        target_id = (rec["folio"], rec["line"], rec["pos"], rec["token"])
        assigned = choose_assigned_match(target_id, selected_sheets, sheet_matches)

        if assigned:
            source = assigned["source"]
            print(
                f"  {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']} "
                f"-> {source['token']} | stripped {source['stripped']} | "
                f"{source['folio']}:{source['line']}:{source['pos']} | "
                f"quire {source.get('quire')}, sheet {source.get('sheet')} | "
                f"ED{assigned['distance']}"
            )

        elif matches_by_target.get(target_id):
            print(
                f"  {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']} "
                "-> MATCH EXISTS BUT NOT IN SELECTED MINIMUM SET"
            )

        else:
            print(
                f"  {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']} "
                "-> NO PRIOR MATCH"
            )

def print_same_page_derived(derived):
    print()
    print("SAME-PAGE ED1-DERIVED TOKENS")
    if not derived:
        print("  none")
        return
    for rec, parent in derived:
        print(
            f"  {rec['token']} | stripped {rec['stripped']} | line {rec['line']}:{rec['pos']} "
            f"-> parent {parent['token']} | stripped {parent['stripped']} | "
            f"line {parent['line']}:{parent['pos']}"
        )

def parse_args():
    parser = argparse.ArgumentParser(description="Analyze Voynich folio word clusters.")
    parser.add_argument("folio", nargs="?", help="Folio to examine, for example f20v or 20v.")
    parser.add_argument("--gui", action="store_true", help="Launch the graphical analyzer interface.")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"), dest="folio_range", help="Analyze all folios from START to END in manuscript order, inclusive.")
    parser.add_argument("--scribe", type=int, choices=range(1, 6), metavar="1-5", help="With --range, analyze only target folios assigned to this scribe number.")
    parser.add_argument("--mappings", default=MAPPINGS_FILE, help=f"Mappings JSON file. Default: {MAPPINGS_FILE}")
    parser.add_argument("--details", action="store_true", help="Print core instance assignments and same-page ED1-derived tokens.")
    parser.add_argument("--max-sheets", type=int, default=6, help="Maximum sheet count for exact cover search before greedy fallback.")
    parser.add_argument("--cluster-details", action="store_true", help="Print full all-match sheet, folio, and quire cluster details.")
    parser.add_argument("--greedy-limit", type=int, default=3, help="Maximum sheets to use in capped greedy best-effort mode when exact full cover fails.")
    return parser.parse_args()

def folios_in_range(words, start, end):
    start = normalize_folio(start)
    end = normalize_folio(end)
    start_key = folio_sort_key(start)
    end_key = folio_sort_key(end)
    if start_key > end_key:
        raise SystemExit(f"Invalid range: {start} comes after {end} in folio order")
    folios = [folio for folio in collect_all_folios(words) if start_key <= folio_sort_key(folio) <= end_key]
    if not folios:
        raise SystemExit(f"No folios found from {start} to {end}")
    return folios

def target_folios_for_args(words, args):
    if args.folio and args.folio_range:
        raise SystemExit("Use either a single folio or --range, not both")

    if args.folio_range:
        target_pages = folios_in_range(words, args.folio_range[0], args.folio_range[1])
        if args.scribe is not None:
            filtered_pages = [folio for folio in target_pages if folio_has_scribe(words, folio, args.scribe)]
            if not filtered_pages:
                start, end = (normalize_folio(value) for value in args.folio_range)
                raise SystemExit(f"No folios assigned to scribe {args.scribe} found from {start} to {end}")
            target_pages = filtered_pages
        return target_pages

    if not args.folio:
        raise SystemExit("Enter a folio or choose range mode.")
    return [normalize_folio(args.folio)]

def run_analysis(args):
    words = load_words(args.mappings)
    target_pages = target_folios_for_args(words, args)
    output = io.StringIO()
    with redirect_stdout(output):
        for index, target_page in enumerate(target_pages):
            if len(target_pages) > 1:
                if index:
                    print()
                print("=" * 72)
            analyze_folio(words, target_page, args)
    return output.getvalue()

def analyze_folio(words, target_page, args):
    contexts = collect_folio_context(words, target_page)
    if not contexts:
        raise SystemExit(f"No occurrences found for folio {target_page} in {args.mappings}")

    instances = collect_page_instances(words, target_page)
    unique = unique_first_occurrences(instances)
    core, derived = remove_same_page_copy_ed1(instances)
    candidates = build_candidates(words, target_page, args.scribe)
    matches_by_target = collect_prior_matches(core, candidates)
    sheet_targets, sheet_matches = build_sheet_targets(matches_by_target)
    coverable_targets = set(matches_by_target)
    selected_sheets, covered_tokens, exact_cover = solve_sheet_cover(
        sheet_targets,
        coverable_targets,
        args.max_sheets,
        args.greedy_limit,
    )
    pruned_sheets = prune_non_core_sheets(
        core,
        selected_sheets,
        sheet_targets,
        candidates,
        coverable_targets
    )
    all_matches = [match for target_matches in matches_by_target.values() for match in target_matches]

    total = len(instances)

    print_folio_summary(target_page, contexts, total, unique, core, derived)
    print(f"PREEXISTING SOURCE CANDIDATES SEARCHED: {len(candidates)}")
    print(f"ALL PRIOR-FOLIO MATCHES FOUND: {len(all_matches)}")

    sheet_groups = summarize_group(all_matches, lambda s: (s.get("quire"), s.get("sheet")))
    folio_groups = summarize_group(all_matches, lambda s: (s.get("folio"), s.get("quire"), s.get("sheet")))
    quire_groups = summarize_group(all_matches, lambda s: (s.get("quire"),))
    if args.details:
        print_top_cluster_summary(sheet_groups, folio_groups)
    print_cover_summary(
        selected_sheets,
        covered_tokens,
        coverable_targets,
        core,
        sheet_targets,
        exact_cover,
        args.max_sheets,
    )
    print()
    print("RETAINED SOURCE SHEETS AFTER CORE PRUNING")
    for sheet in pruned_sheets:
        print(f"  q{sheet[0]}s{sheet[1]}")
    
    classified = classify_sheet_contributions(
        selected_sheets,
        sheet_targets,
        coverable_targets
    )

    print_sheet_classification(target_page, classified)

    print_residue_tokens(
        core,
        selected_sheets,
        sheet_targets,
        sheet_matches,
        coverable_targets
    )

    print_residue_core_recheck(
        core,
        selected_sheets,
        sheet_targets,
        sheet_matches,
        coverable_targets,
        candidates
    )

    print_residue_summary(
        core,
        selected_sheets,
        sheet_targets,
        coverable_targets,
        candidates
    )
    
    print_unresolved_residue_diagnostics(
        core,
        selected_sheets,
        sheet_targets,
        coverable_targets,
        candidates
    )
    if args.cluster_details:
        print_cluster(
            "ALL PRIOR-MATCH SOURCE SHEET CLUSTERING",
            sheet_groups,
            lambda key: f"quire {key[0]}, sheet {key[1]}",
        )
        print_cluster(
            "ALL PRIOR-MATCH SOURCE FOLIO CLUSTERING",
            folio_groups,
            lambda key: f"folio {key[0]} | quire {key[1]}, sheet {key[2]}",
        )
        print_cluster(
            "ALL PRIOR-MATCH SOURCE QUIRE CLUSTERING",
            quire_groups,
            lambda key: f"quire {key[0]}",
        )
    if args.details:
        print_parent_distance_histogram(target_page, core, selected_sheets, sheet_matches)
        print_core_assignments(core, matches_by_target, selected_sheets, sheet_matches)
        print_same_page_derived(derived)

def folio_to_index(folio):
    """
    Convert folio IDs like f27r / f27v / f85r1 into a simple numeric sequence.

    f1r = 2
    f1v = 3
    f2r = 4
    f2v = 5
    etc.
    """
    match = re.match(r"^f?(\d+)([rv])(?:[A-Za-z0-9]*)$", str(folio))
    if not match:
        raise ValueError(f"Cannot convert folio to index: {folio}")

    number = int(match.group(1))
    side = match.group(2)

    return number * 2 + (0 if side == "r" else 1)

def print_parent_distance_histogram(target_page, core, selected_sheets, sheet_matches):
    """
    CORRECT VERSION

    Counts ONE assigned parent per target instance,
    based on the selected sheet cover.
    """

    distance_counts = Counter()
    target_idx = folio_to_index(target_page)

    for rec in core:
        target_id = (rec["folio"], rec["line"], rec["pos"], rec["token"])

        assigned = choose_assigned_match(target_id, selected_sheets, sheet_matches)

        if not assigned:
            continue

        source_folio = assigned["source"]["folio"]
        source_idx = folio_to_index(source_folio)

        distance = target_idx - source_idx

        if distance > 0:
            distance_counts[distance] += 1

    print()
    print("PARENT DISTANCE HISTOGRAM (ASSIGNED PARENTS ONLY)")

    total = sum(distance_counts.values())

    if total == 0:
        print("  none")
        return

    for distance in sorted(distance_counts):
        count = distance_counts[distance]
        pct = (count / total) * 100
        print(f"  dist {distance:>3}: {count:>5} ({pct:5.1f}%)")
        
def classify_sheet_contributions(selected_sheets, sheet_targets, coverable_tokens):
    return classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

def print_sheet_classification(folio, classified):
    print()
    print("SHEET CLASSIFICATION")

    for entry in classified:
        q, s = entry["sheet"]
        adds = entry["adds"]
        tier = entry["tier"]

        print(f"  q{q}s{s}: +{adds}  [{tier}]")
        
def print_residue_tokens(core, selected_sheets, sheet_targets, sheet_matches, coverable_tokens):
    """
    Print ONLY target tokens newly added by RESIDUE sheets.
    """

    print()
    print("RESIDUE TOKENS (NEWLY ADDED BY RESIDUE SHEETS)")

    classified = classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

    core_by_id = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"]): rec
        for rec in core
    }

    residue_items = []

    for entry in classified:
        if entry["tier"] != "RESIDUE":
            continue

        sheet = entry["sheet"]

        for target_id in sorted(
            entry["new_cover"],
            key=lambda tid: (
                folio_sort_key(tid[0]),
                line_sort_key(tid[1]),
                tid[2],
                tid[3],
            )
        ):
            rec = core_by_id[target_id]
            matches = sheet_matches.get(sheet, {}).get(target_id, [])

            if not matches:
                residue_items.append((sheet, rec, None))
                continue

            best = min(
                matches,
                key=lambda match: (
                    match["distance"],
                    folio_sort_key(match["source"]["folio"]),
                    line_sort_key(match["source"]["line"]),
                    match["source"]["pos"],
                    match["source"]["token"],
                )
            )

            residue_items.append((sheet, rec, best))

    if not residue_items:
        print("  none")
        return

    for sheet, rec, match in residue_items:
        if match is None:
            print(
                f"  {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']} "
                f"-> NO MATCH RECORDED FOR q{sheet[0]}s{sheet[1]}"
            )
            continue

        src = match["source"]

        print(
            f"  {rec['token']} | stripped {rec['stripped']} | "
            f"{rec['folio']}:{rec['line']}:{rec['pos']} "
            f"-> {src['token']} | stripped {src['stripped']} | "
            f"{src['folio']}:{src['line']}:{src['pos']} | "
            f"q{sheet[0]}s{sheet[1]} | ED{match['distance']}"
        )
        
def print_residue_core_recheck(core, selected_sheets, sheet_targets, sheet_matches, coverable_tokens, candidates):
    """
    For each RESIDUE token, recheck whether it can be explained
    from CORE sheets only.

    Matching order:
      1. CORE ED0 / exact stripped match
      2. CORE ED1
      3. CORE ED2 only if stripped token length >= 6
      4. NOT RESOLVED
    """

    print()
    print("RESIDUE CORE-RECHECK")

    classified = classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

    core_sheets = {
        entry["sheet"]
        for entry in classified
        if entry["tier"] == "CORE"
    }

    residue_target_ids = set()

    for entry in classified:
        if entry["tier"] == "RESIDUE":
            residue_target_ids.update(entry["new_cover"])

    if not residue_target_ids:
        print("  none")
        return

    if not core_sheets:
        print("  no CORE sheets selected")
        return

    core_by_id = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"]): rec
        for rec in core
    }

    core_candidates = [
        cand for cand in candidates
        if (cand.get("quire"), cand.get("sheet")) in core_sheets
    ]

    for target_id in sorted(
        residue_target_ids,
        key=lambda tid: (
            folio_sort_key(tid[0]),
            line_sort_key(tid[1]),
            tid[2],
            tid[3],
        )
    ):
        rec = core_by_id[target_id]

        max_d = 2 if len(rec["stripped"]) >= 6 else 1

        best = None
        best_d = None
        best_key = None

        for allowed_d in (0, 1, 2):
            if allowed_d > max_d:
                continue

            for cand in core_candidates:
                d = edit_distance(rec["stripped"], cand["stripped"], allowed_d)

                if d != allowed_d:
                    continue

                cand_key = (
                    folio_sort_key(cand["folio"]),
                    line_sort_key(cand["line"]),
                    cand["pos"],
                    cand["token"],
                )

                if best is None or cand_key < best_key:
                    best = cand
                    best_d = d
                    best_key = cand_key

            if best is not None:
                break

        if best is None:
            print(
                f"  NOT RESOLVED: {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']}"
            )
        else:
            print(
                f"  RESOLVED ED{best_d}: {rec['token']} | stripped {rec['stripped']} | "
                f"{rec['folio']}:{rec['line']}:{rec['pos']} "
                f"-> {best['token']} | stripped {best['stripped']} | "
                f"{best['folio']}:{best['line']}:{best['pos']} | "
                f"q{best.get('quire')}s{best.get('sheet')}"
            )     

def print_residue_summary(core, selected_sheets, sheet_targets, coverable_tokens, candidates):
    print()
    print("RESIDUE SUMMARY")

    classified = classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

    core_sheets = {
        entry["sheet"]
        for entry in classified
        if entry["tier"] == "CORE"
    }

    core_candidates = [
        cand for cand in candidates
        if (cand.get("quire"), cand.get("sheet")) in core_sheets
    ]

    total = 0
    ed0 = ed1 = ed2 = unresolved = 0

    core_by_id = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"]): rec
        for rec in core
    }

    for entry in classified:
        if entry["tier"] != "RESIDUE":
            continue

        for tid in entry["new_cover"]:
            total += 1
            rec = core_by_id[tid]

            max_d = 2 if len(rec["stripped"]) >= 6 else 1

            match, d = find_best_core_match_for_stripped(
                rec["stripped"],
                core_candidates,
                max_d
            )

            if match is None:
                unresolved += 1
            elif d == 0:
                ed0 += 1
            elif d == 1:
                ed1 += 1
            elif d == 2:
                ed2 += 1

    print(f"  total: {total}")
    print(f"  ED0: {ed0}")
    print(f"  ED1: {ed1}")
    print(f"  ED2: {ed2}")
    print(f"  unresolved: {unresolved}")
        
def classify_selected_sheets(selected_sheets, sheet_targets, coverable_tokens):
    """
    Return selected sheet classifications using incremental-add logic.

    CORE: adds >= 10
    SECONDARY: adds 4–9
    RESIDUE: adds <= 3
    """
    coverable = set(coverable_tokens)
    running = set()
    classified = []

    for sheet in selected_sheets:
        sheet_cover = sheet_targets[sheet] & coverable
        new_cover = sheet_cover - running
        running.update(sheet_cover)

        adds = len(new_cover)

        if adds >= 10:
            tier = "CORE"
        elif adds >= 4:
            tier = "SECONDARY"
        else:
            tier = "RESIDUE"

        classified.append({
            "sheet": sheet,
            "adds": adds,
            "tier": tier,
            "new_cover": new_cover,
        })

    return classified

def find_best_core_match_for_stripped(stripped, core_candidates, max_d):
    """
    Find best match for an arbitrary stripped string against CORE-sheet candidates.

    Used by unresolved-residue diagnostics.
    """
    best = None
    best_d = None
    best_key = None

    for allowed_d in range(max_d + 1):
        for cand in core_candidates:
            d = edit_distance(stripped, cand["stripped"], allowed_d)

            if d != allowed_d:
                continue

            cand_key = (
                folio_sort_key(cand["folio"]),
                line_sort_key(cand["line"]),
                cand["pos"],
                cand["token"],
            )

            if best is None or cand_key < best_key:
                best = cand
                best_d = d
                best_key = cand_key

        if best is not None:
            break

    return best, best_d

def prune_non_core_sheets(core, selected_sheets, sheet_targets, candidates, coverable_tokens):
    """
    Remove sheets whose contributions are fully explainable from CORE sheets.
    """

    classified = classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

    core_sheets = {
        entry["sheet"]
        for entry in classified
        if entry["tier"] == "CORE"
    }

    if not core_sheets:
        return selected_sheets

    core_candidates = [
        cand for cand in candidates
        if (cand.get("quire"), cand.get("sheet")) in core_sheets
    ]

    core_by_id = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"]): rec
        for rec in core
    }

    pruned = []

    for entry in classified:
        sheet = entry["sheet"]

        if sheet in core_sheets:
            pruned.append(sheet)
            continue

        all_explainable = True

        for tid in entry["new_cover"]:
            rec = core_by_id[tid]

            max_d = 2 if len(rec["stripped"]) >= 6 else 1

            match, _ = find_best_core_match_for_stripped(
                rec["stripped"],
                core_candidates,
                max_d
            )

            if match is None:
                all_explainable = False
                break

        if not all_explainable:
            pruned.append(sheet)

    return pruned

def print_unresolved_residue_diagnostics(core, selected_sheets, sheet_targets, coverable_tokens, candidates):
    """
    For residue tokens that do NOT resolve from CORE by ED0/ED1/limited ED2,
    run extra diagnostics:

      1. split/composite check
      2. two-step mutation flag

    This does NOT change the solver.
    It only prints diagnostic evidence.
    """

    print()
    print("UNRESOLVED RESIDUE DIAGNOSTICS")

    classified = classify_selected_sheets(
        selected_sheets,
        sheet_targets,
        coverable_tokens
    )

    core_sheets = {
        entry["sheet"]
        for entry in classified
        if entry["tier"] == "CORE"
    }

    residue_target_ids = set()
    for entry in classified:
        if entry["tier"] == "RESIDUE":
            residue_target_ids.update(entry["new_cover"])

    if not residue_target_ids:
        print("  none")
        return

    core_by_id = {
        (rec["folio"], rec["line"], rec["pos"], rec["token"]): rec
        for rec in core
    }

    core_candidates = [
        cand for cand in candidates
        if (cand.get("quire"), cand.get("sheet")) in core_sheets
    ]

    unresolved = []

    for target_id in residue_target_ids:
        rec = core_by_id[target_id]

        max_d = 2 if len(rec["stripped"]) >= 6 else 1
        match, _ = find_best_core_match_for_stripped(
            rec["stripped"],
            core_candidates,
            max_d
        )

        if match is None:
            unresolved.append(rec)

    if not unresolved:
        print("  none")
        return

    for rec in sorted(
        unresolved,
        key=lambda r: (
            folio_sort_key(r["folio"]),
            line_sort_key(r["line"]),
            r["pos"],
            r["token"],
        )
    ):
        print(
            f"  UNRESOLVED: {rec['token']} | stripped {rec['stripped']} | "
            f"{rec['folio']}:{rec['line']}:{rec['pos']}"
        )

        stripped = rec["stripped"]
        split_hits = []

        for i in range(2, len(stripped) - 1):
            left = stripped[:i]
            right = stripped[i:]

            left_max = 1 if len(left) < 6 else 2
            right_max = 1 if len(right) < 6 else 2

            left_match, left_d = find_best_core_match_for_stripped(
                left,
                core_candidates,
                left_max
            )
            right_match, right_d = find_best_core_match_for_stripped(
                right,
                core_candidates,
                right_max
            )

            if left_match and right_match:
                split_hits.append((
                    left,
                    right,
                    left_match,
                    left_d,
                    right_match,
                    right_d,
                ))

        if split_hits:
            print("    SPLIT / COMPOSITE CANDIDATES:")

            for left, right, left_match, left_d, right_match, right_d in split_hits[:5]:
                print(
                    f"      {left} + {right} | "
                    f"left ED{left_d}: {left_match['token']} "
                    f"({left_match['folio']}:{left_match['line']}:{left_match['pos']}) | "
                    f"right ED{right_d}: {right_match['token']} "
                    f"({right_match['folio']}:{right_match['line']}:{right_match['pos']})"
                )
        else:
            print("    SPLIT / COMPOSITE CANDIDATES: none")

        # Two-step mutation diagnostic:
        # If it failed under strict core recheck, but would pass at ED2
        # only because length allowed it, report that separately.
        if len(stripped) >= 6:
            loose_match, loose_d = find_best_core_match_for_stripped(
                stripped,
                core_candidates,
                2
            )

            if loose_match:
                print(
                    f"    TWO-STEP / ED2-LIKE CANDIDATE: ED{loose_d} "
                    f"{loose_match['token']} | stripped {loose_match['stripped']} | "
                    f"{loose_match['folio']}:{loose_match['line']}:{loose_match['pos']}"
                )
            else:
                print("    TWO-STEP / ED2-LIKE CANDIDATE: none")
        else:
            print("    TWO-STEP / ED2-LIKE CANDIDATE: skipped; stripped length < 5")
        
class VoynichAnalyzerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Voynich Analyzer v1")
        self.root.geometry("1120x780")
        self.root.minsize(860, 560)

        self.mappings_var = tk.StringVar(value=MAPPINGS_FILE)
        self.mode_var = tk.StringVar(value="single")
        self.folio_var = tk.StringVar(value="f20v")
        self.range_start_var = tk.StringVar(value="f1r")
        self.range_end_var = tk.StringVar(value="f1v")
        self.scribe_var = tk.StringVar(value="All")
        self.max_sheets_var = tk.IntVar(value=6)
        self.greedy_limit_var = tk.IntVar(value=3)
        self.details_var = tk.BooleanVar(value=False)
        self.cluster_details_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.available_folios = []
        self.worker = None

        self._build()
        self._load_folios(show_errors=False)
        self._sync_mode()

    def _build(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        file_row = ttk.Frame(outer)
        file_row.grid(row=0, column=0, sticky="ew")
        file_row.columnconfigure(1, weight=1)
        ttk.Label(file_row, text="Mappings JSON").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(file_row, textvariable=self.mappings_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(file_row, text="...", width=3, command=self._choose_mappings).grid(row=0, column=2, padx=(6, 0))

        controls = ttk.Frame(outer)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        controls.columnconfigure(11, weight=1)

        ttk.Radiobutton(controls, text="Single folio", variable=self.mode_var, value="single", command=self._sync_mode).grid(row=0, column=0, sticky="w")
        self.folio_entry = ttk.Combobox(controls, textvariable=self.folio_var, width=10, state="readonly")
        self.folio_entry.grid(row=0, column=1, sticky="w", padx=(6, 16))

        ttk.Radiobutton(controls, text="Range", variable=self.mode_var, value="range", command=self._sync_mode).grid(row=0, column=2, sticky="w")
        self.range_start_entry = ttk.Combobox(controls, textvariable=self.range_start_var, width=10, state="readonly")
        self.range_start_entry.grid(row=0, column=3, sticky="w", padx=(6, 4))
        ttk.Label(controls, text="to").grid(row=0, column=4)
        self.range_end_entry = ttk.Combobox(controls, textvariable=self.range_end_var, width=10, state="readonly")
        self.range_end_entry.grid(row=0, column=5, sticky="w", padx=(4, 16))

        ttk.Label(controls, text="Scribe").grid(row=0, column=6, sticky="w")
        self.scribe_combo = ttk.Combobox(
            controls,
            textvariable=self.scribe_var,
            values=["All", "1", "2", "3", "4", "5"],
            width=6,
            state="readonly",
        )
        self.scribe_combo.grid(row=0, column=7, sticky="w", padx=(6, 16))

        ttk.Label(controls, text="Max sheets").grid(row=0, column=8, sticky="w")
        ttk.Spinbox(controls, from_=1, to=20, textvariable=self.max_sheets_var, width=5).grid(row=0, column=9, sticky="w", padx=(6, 16))

        ttk.Label(controls, text="Greedy limit").grid(row=0, column=10, sticky="w")
        ttk.Spinbox(controls, from_=1, to=20, textvariable=self.greedy_limit_var, width=5).grid(row=0, column=11, sticky="w", padx=(6, 0))

        options = ttk.Frame(outer)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(options, text="Core assignments and same-page ED1 details", variable=self.details_var).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="Full cluster details", variable=self.cluster_details_var).pack(side=tk.LEFT, padx=(18, 0))

        report_frame = ttk.Frame(outer)
        report_frame.grid(row=3, column=0, sticky="nsew")
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)

        yscroll = ttk.Scrollbar(report_frame, orient=tk.VERTICAL)
        xscroll = ttk.Scrollbar(report_frame, orient=tk.HORIZONTAL)
        self.report_text = tk.Text(
            report_frame,
            wrap="none",
            font=("Consolas", 10),
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )
        yscroll.configure(command=self.report_text.yview)
        xscroll.configure(command=self.report_text.xview)
        self.report_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        bottom = ttk.Frame(outer)
        bottom.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.run_button = ttk.Button(bottom, text="Run", command=self._run)
        self.run_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(bottom, text="Save Report", command=self._save_report).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(bottom, text="Clear", command=self._clear_report).grid(row=0, column=3, padx=(8, 0))

    def _sync_mode(self):
        single = self.mode_var.get() == "single"
        self.folio_entry.configure(state="readonly" if single else tk.DISABLED)
        self.range_start_entry.configure(state=tk.DISABLED if single else "readonly")
        self.range_end_entry.configure(state=tk.DISABLED if single else "readonly")

    def _choose_mappings(self):
        initial = Path(self.mappings_var.get())
        filename = filedialog.askopenfilename(
            title="Choose mappings JSON",
            initialdir=str(initial.parent if initial.parent.exists() else Path.cwd()),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if filename:
            self.mappings_var.set(filename)
            self._load_folios(show_errors=True)

    def _load_folios(self, show_errors=True):
        try:
            folios = collect_all_folios(load_words(self.mappings_var.get().strip() or MAPPINGS_FILE))
        except Exception as exc:
            self.available_folios = []
            for combo in (self.folio_entry, self.range_start_entry, self.range_end_entry):
                combo.configure(values=())
            self.status_var.set("Could not load folios from mappings file")
            if show_errors:
                messagebox.showerror("Voynich Analyzer", str(exc))
            return

        self.available_folios = folios
        for combo in (self.folio_entry, self.range_start_entry, self.range_end_entry):
            combo.configure(values=folios)

        if folios:
            if self.folio_var.get() not in folios:
                self.folio_var.set(folios[0])
            if self.range_start_var.get() not in folios:
                self.range_start_var.set(folios[0])
            if self.range_end_var.get() not in folios:
                self.range_end_var.set(folios[-1])
            self.status_var.set(f"Loaded {len(folios)} folios")

    def _args_from_controls(self):
        scribe = None if self.scribe_var.get() == "All" else int(self.scribe_var.get())
        if self.mode_var.get() == "range":
            folio = None
            folio_range = [self.range_start_var.get().strip(), self.range_end_var.get().strip()]
        else:
            folio = self.folio_var.get().strip()
            folio_range = None
        return argparse.Namespace(
            folio=folio,
            gui=True,
            folio_range=folio_range,
            scribe=scribe,
            mappings=self.mappings_var.get().strip() or MAPPINGS_FILE,
            details=self.details_var.get(),
            max_sheets=int(self.max_sheets_var.get()),
            cluster_details=self.cluster_details_var.get(),
            greedy_limit=int(self.greedy_limit_var.get()),
        )

    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        args = self._args_from_controls()
        self.run_button.configure(state=tk.DISABLED)
        self.status_var.set("Running analysis...")
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, "Running analysis...\n")
        self.worker = threading.Thread(target=self._run_worker, args=(args,), daemon=True)
        self.worker.start()

    def _run_worker(self, args):
        try:
            report = run_analysis(args)
            self.root.after(0, self._finish_run, report, None)
        except BaseException as exc:
            if isinstance(exc, SystemExit):
                message = str(exc) or "Analysis stopped."
                detail = message
            else:
                message = str(exc) or exc.__class__.__name__
                detail = traceback.format_exc()
            self.root.after(0, self._finish_run, detail, message)

    def _finish_run(self, report, error):
        self.run_button.configure(state=tk.NORMAL)
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert(tk.END, report)
        if error:
            self.status_var.set("Analysis failed")
            messagebox.showerror("Voynich Analyzer", error)
        else:
            self.status_var.set("Analysis complete")

    def _save_report(self):
        report = self.report_text.get("1.0", tk.END).rstrip()
        if not report:
            messagebox.showinfo("Voynich Analyzer", "There is no report to save.")
            return
        filename = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="voynich_analysis_report.txt",
        )
        if not filename:
            return
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        self.status_var.set(f"Saved report: {filename}")

    def _clear_report(self):
        self.report_text.delete("1.0", tk.END)
        self.status_var.set("Ready")


def launch_gui():
    root = tk.Tk()
    VoynichAnalyzerGUI(root)
    root.mainloop()


def main():
    args = parse_args()
    if args.gui or len(sys.argv) == 1:
        launch_gui()
        return
    print(run_analysis(args), end="")

if __name__ == "__main__":
    main()
