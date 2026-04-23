import traceback
import sys
from graphiti import Graphiti
try:
    g = Graphiti()
    print('Graphiti initialized')
except Exception:
    traceback.print_exc()