"""Record exact unittest outcomes for unattended CI diagnosis."""
import io,json,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
def main():
    suite=unittest.defaultTestLoader.discover(str(root/'tests'))
    output=io.StringIO();result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    (root/'build').mkdir(exist_ok=True)
    (root/'build/unit-tests.log').write_text(output.getvalue(),encoding='utf-8')
    (root/'build/unit-tests.json').write_text(json.dumps({'run':result.testsRun,'failed':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)}),encoding='utf-8')
    print(output.getvalue(),flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)

if __name__=='__main__':main()
