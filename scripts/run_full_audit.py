import os
import subprocess
import time

AUDIT_DIR = f"reports/model_value_audit_20260621_195300"
os.makedirs(AUDIT_DIR, exist_ok=True)

scripts = [
    "scripts/robustness_tests_pt1.py",
    "scripts/robustness_tests_pt2.py",
    "scripts/robustness_tests_pt3.py",
    "scripts/robustness_tests_pt4.py",
    "scripts/compare_01_vs_02.py"
]

for script in scripts:
    with open(script, "r") as f:
        content = f.read()
    
    # Replace hardcoded reports/ with the new directory
    content = content.replace('"reports/', f'"{AUDIT_DIR}/')
    content = content.replace('"python3",', '".venv/bin/python3",')
    
    # compare_01_vs_02.py prints to stdout. We want to save it.
    if "compare" in script:
        content += f'\n    with open("{AUDIT_DIR}/comparison.txt", "w") as out_f:\n'
        content += '        import sys\n'
        content += '        sys.stdout = out_f\n'
        content += '        main()\n'
        content = content.replace('if __name__ == "__main__":\n    main()', '')

    tmp_script = f"/tmp/{os.path.basename(script)}"
    with open(tmp_script, "w") as f:
        f.write(content)
    
    print(f"Running {tmp_script}...")
    subprocess.run([".venv/bin/python3", tmp_script], check=True, env={**os.environ, "PYTHONPATH": "."})

print("Audit scripts completed.")
