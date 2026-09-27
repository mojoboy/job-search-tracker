"""
The public demo: the Job Search Tracker on made-up sample data, with no database to set up.
Each browser tab gets its own copy, so visitors can try everything without affecting anyone else.

    streamlit run demo.py
"""

from app import main

main(demo=True)
