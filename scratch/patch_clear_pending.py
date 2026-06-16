import sys

def patch_main():
    with open("main.py", "r") as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        # Find where we update pending_book_moves
        if "pending_book_moves[:] = still_pending" in line:
            # We want to clear expired ones here too to be safe, but they are already filtered by the loop.
            pass

    with open("main.py", "w") as f:
        f.writelines(lines)
        
patch_main()
