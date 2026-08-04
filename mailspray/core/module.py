"""NetExec-style post-auth module framework.

A module runs AFTER a successful authentication against a supported protocol.
The CLI resolves `-M <name>`, authenticates with the given creds, and calls
`module.on_auth(ctx, handle)` where `handle` is the live connection/session the
protocol's authenticate() returned.

Modules live in the `mailspray.modules` package; discovery is automatic — drop a
new file defining a BaseMSModule subclass and it is picked up by `-L` / `-M`.
"""

import importlib
import pkgutil


class BaseMSModule:
    name = ""                     # -M <name>; defaults to the module filename
    description = ""
    supported_protocols = []      # e.g. ["imap"] or ["owa", "ews"]
    opts_help = {}                # {"key": "help text"} for -L / --options

    def options(self, opts):
        """Consume the parsed -O KEY=VAL dict. Override to validate/store options."""
        self.opts = dict(opts or {})

    def on_auth(self, ctx, handle):
        """Run against an authenticated handle. Override in the module."""
        raise NotImplementedError


class ModuleContext:
    """Passed to on_auth(). Logging callables are injected by the CLI to avoid
    a circular import with cli.py."""

    def __init__(self, protocol, host, username, options, db, module_name,
                 base_url=None, timeout=15,
                 log_info=None, log_good=None, log_warn=None):
        self.protocol = protocol
        self.host = host
        self.username = username
        self.options = dict(options or {})
        self.db = db
        self.module_name = module_name
        self.base_url = base_url
        self.timeout = timeout
        self._noop = lambda *a, **k: None
        self.log_info = log_info or self._noop
        self.log_good = log_good or self._noop
        self.log_warn = log_warn or self._noop
        self.loot_count = 0

    def emit_loot(self, category, key, value="", source=""):
        """Report a finding: print it and persist to the workspace DB (best-effort)."""
        self.loot_count += 1
        shown = key if not value else f"{key} = {value}"
        self.log_good(f"[{category}] {shown}" + (f"  ({source})" if source else ""))
        if self.db is not None:
            self.db.add_loot(
                self.module_name, self.protocol, self.host, self.username,
                category, str(key), str(value), str(source),
            )


# ── Discovery ───────────────────────────────────────────────────────

def _iter_module_classes():
    """Yield (name, class) for every BaseMSModule subclass under mailspray.modules."""
    import mailspray.modules as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"mailspray.modules.{info.name}")
        for attr in vars(mod).values():
            if (isinstance(attr, type)
                    and issubclass(attr, BaseMSModule)
                    and attr is not BaseMSModule):
                name = attr.name or info.name
                yield name, attr


def list_modules():
    """Return {name: class} of all discovered modules, sorted by name."""
    found = {}
    for name, cls in _iter_module_classes():
        found[name] = cls
    return dict(sorted(found.items()))


def get_module(name):
    """Return an INSTANCE of the named module, or None if not found."""
    for mod_name, cls in _iter_module_classes():
        if mod_name == name:
            inst = cls()
            if not getattr(inst, "name", ""):
                inst.name = mod_name
            return inst
    return None
