"""Generate golden datasets + answer key. Usage: python generate_golden.py [--big]
Row indexes in the answer key are 0-based data rows (header excluded)."""
import csv, json, random, sys, os
from datetime import date, timedelta

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
CITIES = ["Mumbai", "Delhi", "Pune", "Bengaluru", "Chennai", "Hyderabad"]
STATUS = ["active", "inactive", "pending"]
COLS = ["customer_id", "name", "email", "phone", "city", "age", "signup_date", "end_date", "amount", "status"]
FIRST = ["Aarav", "Diya", "Rohan", "Isha", "Kabir", "Meera", "Vikram", "Anaya", "Arjun", "Sara"]
LAST = ["Sharma", "Patil", "Khan", "Iyer", "Rao", "Gupta", "Nair", "Joshi", "Mehta", "Singh"]

def make_clean(n=1000):
    rows = []
    for i in range(n):
        fn, ln = random.choice(FIRST), random.choice(LAST)
        start = date(2022, 1, 1) + timedelta(days=random.randint(0, 900))
        end = start + timedelta(days=random.randint(30, 400))
        rows.append([
            f"C{i+1:05d}", f"{fn} {ln}", f"{fn.lower()}.{ln.lower()}{i+1}@example.com",
            f"+91{random.randint(7000000000, 9999999999)}", random.choice(CITIES),
            random.randint(18, 70), start.isoformat(), end.isoformat(),
            round(random.uniform(500, 5000), 2), random.choice(STATUS)])
    return rows

def write_csv(path, rows, cols=COLS, encoding="utf-8"):
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)

def main():
    clean = make_clean()
    write_csv(os.path.join(HERE, "golden_clean.csv"), clean)

    dirty = [r[:] for r in clean]
    idx = list(range(len(dirty))); random.shuffle(idx)
    take = lambda k: [idx.pop() for _ in range(k)]
    key = []
    def mark(t, rows, col): 
        for r in rows: key.append({"defect": t, "row": r, "column": col})

    # disguised nulls: 10 N/A in city, 10 "-" in phone, 10 999 in age
    for r in (a := take(10)): dirty[r][4] = "N/A"
    mark("disguised_null", a, "city")
    for r in (a := take(10)): dirty[r][3] = "-"
    mark("disguised_null", a, "phone")
    for r in (a := take(10)): dirty[r][5] = 999
    mark("disguised_null", a, "age")
    # bad emails: 20
    bad = ["not-an-email", "user@@example.com", "missing.domain@", "@nouser.com", "spaces in@mail.com"]
    for r in (a := take(20)): dirty[r][2] = random.choice(bad)
    mark("invalid_format", a, "email")
    # outliers: 15 huge amounts
    for r in (a := take(15)): dirty[r][8] = round(random.uniform(500000, 900000), 2)
    mark("outlier", a, "amount")
    # inconsistent categories: Mumbai rows only, 40 variants
    mumbai = [i for i in idx if dirty[i][4] == "Mumbai"][:40]
    for r in mumbai:
        dirty[r][4] = random.choice(["mumbai", "MUMBAI"]); idx.remove(r)
    mark("inconsistent_category", mumbai, "city")
    # end before start: 25
    for r in (a := take(25)):
        s = date.fromisoformat(dirty[r][6]); dirty[r][7] = (s - timedelta(days=random.randint(1, 60))).isoformat()
    mark("rule_violation_end_before_start", a, "end_date")
    # duplicates: copy 50 untouched rows and append
    src = take(50); n0 = len(dirty)
    for k, r in enumerate(src):
        dirty.append(dirty[r][:]); key.append({"defect": "duplicate", "row": n0 + k, "column": None, "duplicate_of_row": r})
    write_csv(os.path.join(HERE, "golden_dirty.csv"), dirty)
    summary = {}
    for k in key: summary[k["defect"]] = summary.get(k["defect"], 0) + 1
    json.dump({"rows": len(dirty), "columns": COLS, "summary": summary, "planted": key},
              open(os.path.join(HERE, "golden_dirty_answer_key.json"), "w"), indent=1)

    # edge cases
    E = os.path.join(HERE, "edge"); os.makedirs(E, exist_ok=True)
    open(os.path.join(E, "empty.csv"), "w").close()
    write_csv(os.path.join(E, "header_only.csv"), [])
    write_csv(os.path.join(E, "one_column.csv"), [[i] for i in range(50)], ["id"])
    write_csv(os.path.join(E, "latin1.csv"), [["José", "Zoë"], ["Müller", "Çelik"]], ["first", "last"], "latin-1")
    write_csv(os.path.join(E, "utf16.csv"), [["Aarav", "Mumbai"], ["Diya", "Pune"]], ["name", "city"], "utf-16")
    write_csv(os.path.join(E, "mixed_types.csv"), [[1, "a"], ["two", "b"], [3.5, "c"], [None, "d"], ["2024-01-01", "e"]], ["value", "tag"])
    write_csv(os.path.join(E, "wide_500_cols.csv"), [[random.randint(0, 99) for _ in range(500)] for _ in range(200)], [f"c{i}" for i in range(500)])
    open(os.path.join(E, "corrupt.parquet"), "wb").write(b"PAR1 this is not really parquet")
    if "--big" in sys.argv:
        with open(os.path.join(E, "big_1m.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(COLS)
            for i in range(1_000_000):
                w.writerow([f"C{i:07d}", "Name", f"u{i}@example.com", "+919999999999", "Pune", 30, "2023-01-01", "2023-06-01", 1000.0, "active"])
    print("done", summary)

main()
