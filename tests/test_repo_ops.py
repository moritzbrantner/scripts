from __future__ import annotations
import json, subprocess, tempfile, unittest
from pathlib import Path
import repo_ops as r

def run(*args,cwd): subprocess.run(args,cwd=cwd,check=True,capture_output=True,text=True)
def make_repo(root,name,remote=None):
    p=root/name;p.mkdir();run("git","init","-b","main",cwd=p);run("git","config","user.email","t@example.com",cwd=p);run("git","config","user.name","T",cwd=p);(p/"README.md").write_text("x\n");run("git","add",".",cwd=p);run("git","commit","-m","init",cwd=p)
    if remote:run("git","remote","add","origin",remote,cwd=p)
    return p

class Discovery(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(r.slug_from_remote("git@github.com:moritzbrantner/scripts.git"),"moritzbrantner/scripts")
        self.assertEqual(r.slug_from_remote("https://github.com/moritzbrantner/scripts.git"),"moritzbrantner/scripts")
    def test_auto_single_and_fleet(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=make_repo(root,"a","https://github.com/moritzbrantner/a.git");b=make_repo(root,"b")
            self.assertEqual([x.name for x in r.discover(a/"src" if (a/"src").mkdir() is None else a)], ["a"])
            self.assertEqual([x.name for x in r.discover(root)], ["a","b"])
    def test_filters(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);make_repo(root,"lab-a");make_repo(root,"lab-b");make_repo(root,"app")
            self.assertEqual([x.name for x in r.discover(root,include=["lab-*"],exclude=["*-b"])],["lab-a"])

class Analysis(unittest.TestCase):
    def test_workflow_pin(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);w=p/".github/workflows";w.mkdir(parents=True);(w/"ci.yml").write_text("steps:\n- uses: actions/checkout@v4\n- uses: x/y@0123456789012345678901234567890123456789\n")
            self.assertEqual(len(r.workflow_findings(p)),1)
    def test_dependency_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=make_repo(root,"a");b=make_repo(root,"b");(a/"package.json").write_text(json.dumps({"dependencies":{"react":"1"}}));(b/"package.json").write_text(json.dumps({"dependencies":{"react":"2"}}))
            out=r.cmd_dependency_sync([r.Target(a,"a"),r.Target(b,"b")],None)[0]
            self.assertEqual(out.status,"attention");self.assertIn("npm:react",out.data["drift"])
    def test_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=make_repo(root,"a");b=make_repo(root,"b");src="def f(x):\n    y=x.strip()\n    y=y.casefold()\n    y=y.replace('-', '_')\n    return y\n";(a/"a.py").write_text(src);(b/"b.py").write_text(src)
            self.assertEqual(len(r.duplicate_groups([r.Target(a,"a"),r.Target(b,"b")])),1)
    def test_graph_path_edge(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=make_repo(root,"a");b=make_repo(root,"b");(a/"package.json").write_text(json.dumps({"dependencies":{"b":"file:../b"}}));g=r.graph_data([r.Target(a,"a"),r.Target(b,"b")]);self.assertIn({"from":"a","to":"b","kind":"npm:dependencies:path"},g["edges"])

class Safety(unittest.TestCase):
    def test_bootstrap_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=make_repo(root,"a");t=r.Target(p,"a")
            class A:apply=False
            self.assertEqual(r.cmd_repo_bootstrap([t],A())[0].status,"planned");self.assertFalse((p/"renovate.json").exists())
            A.apply=True;r.cmd_repo_bootstrap([t],A());self.assertTrue((p/"renovate.json").exists())
            (p/".editorconfig").write_text("custom\n");r.cmd_repo_bootstrap([t],A());self.assertEqual((p/".editorconfig").read_text(),"custom\n")
    def test_clean_ignored_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=make_repo(root,"a");(p/".gitignore").write_text("dist/\n");run("git","add",".gitignore",cwd=p);run("git","commit","-m","ignore",cwd=p);(p/"dist").mkdir();(p/"dist/x").write_text("x");(p/"build").mkdir();(p/"build/x").write_text("x")
            class A:apply=False;candidate=[]
            out=r.cmd_clean([r.Target(p,"a")],A())[0];self.assertIn("dist",out.data["candidates"]);self.assertIn("build",out.data["skipped"])
            A.apply=True;r.cmd_clean([r.Target(p,"a")],A());self.assertFalse((p/"dist").exists());self.assertTrue((p/"build").exists())

class Acceptance(unittest.TestCase):
    def test_missing_checks_fail_closed(self):
        t=r.Target(Path("/tmp/x"),"x","o/x");p={"number":1,"isDraft":False,"headRefOid":"a"*40,"mergeStateStatus":"CLEAN","reviewDecision":"APPROVED","unresolved":0,"checks":[]};self.assertIn("required-check-evidence-empty",{x.code for x in r.pr_blockers(t,p)})
    def test_clean_state(self):
        t=r.Target(Path("/tmp/x"),"x","o/x");p={"number":1,"isDraft":False,"headRefOid":"a"*40,"mergeStateStatus":"CLEAN","reviewDecision":"APPROVED","unresolved":0,"checks":[{"name":"Validate","state":"SUCCESS","bucket":"pass"}]};self.assertEqual(r.pr_blockers(t,p),[])

class Cli(unittest.TestCase):
    def test_all_commands_unique(self): self.assertEqual(len(r.COMMANDS),33);self.assertEqual(len(set(r.COMMANDS)),33)
    def test_requested_commands_present(self):
        requested={"repo-health","pr-accept","pr-stack-status","fleet-status","repo-bootstrap","repo-drift","workflow-pin","dependency-sync","duplicate-code-scan","extract-candidate","pages-smoke","pages-inventory","dogfood-check","changed-only","ci-reproduce","toolchain-fingerprint","rust-workspace-audit","ts-workspace-audit","expo-readiness","dotnet-api-audit","roadmap-next","stale-work","issue-from-finding","release-evidence","repo-graph","ownership-check","workspace-clean","batch-pr","merge-green"};self.assertTrue(requested.issubset(set(r.COMMANDS)))
    def test_aliases(self):
        root=Path(__file__).resolve().parents[1];self.assertTrue(all((root/"bin"/x).is_file() for x in r.COMMANDS))

if __name__=="__main__":unittest.main()
