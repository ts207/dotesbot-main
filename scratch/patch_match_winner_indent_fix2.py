import sys

def patch_main():
    with open("main.py", "r") as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        if "expected_move *= sensitivity" in line and i > 1700:
            lines[i] = "                    expected_move *= sensitivity\n"

    with open("main.py", "w") as f:
        f.writelines(lines)
        
patch_main()
