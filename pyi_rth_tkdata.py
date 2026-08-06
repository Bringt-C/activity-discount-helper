"""Point bundled tkinter at the Tcl/Tk data inside PyInstaller's temp folder."""

import os
import sys


bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
os.environ["TCL_LIBRARY"] = os.path.join(bundle_dir, "_tcl_data")
os.environ["TK_LIBRARY"] = os.path.join(bundle_dir, "_tk_data")
