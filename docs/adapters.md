# Game adapters

Adapters provide optional game-specific checks around the generic file deployment engine. The generic adapter accepts an accessible installation directory and deliberately makes no claim about mod compatibility. Profile membership and file mappings are stored by registered game ID, while the imported mod and its source files remain in the shared library.

## Contract

Implement `GameAdapter` from `modforge.games.base`:

- `game_id`: stable identifier used in the database
- `name`: human-readable adapter name
- `validate_root(root)`: return `(valid, explanation)` without modifying files
- `validate_relative_path(value)`: retain safe relative-path validation; stricter adapters may add rules
- `supported_locations()`: informational locations only; generic deployment does not require them

An adapter must not bypass `DeploymentManager` path containment, no-follow checks, backup, journaling, or verification. Persisted destination mappings are revalidated by the adapter during deployment. The versioned database also records deployed-file ownership by game, profile, transaction, fingerprint, and contributing mod IDs. `DeploymentManager.apply_profile` restores the previous successful deployment for that installation before applying the selected profile; it refuses ambiguous external modifications and retains recovery material. This transition consists of multiple durable file transactions, not one filesystem-atomic operation.

The current registry selects the built-in Cyberpunk adapter for Cyberpunk registrations and otherwise uses `GenericFileAdapter`. Third-party entry-point discovery and an adapter compatibility/version protocol are future work; do not install an adapter from an archive or treat untrusted plugin code as safe.

## Adding an adapter

1. Add an adapter module with read-only validation and stable ID.
2. Add an explicit, reviewed selection path in the registry; do not infer games from display names.
3. Test valid, missing, malformed, and unsupported roots using `tmp_path` fixtures.
4. Add generic deployment tests proving that path safety still applies.
5. Describe which loader/layout behavior is verified and which remains manual.

Cyberpunk 2077 currently has a root check for `bin/x64`. Other named games have no specialized adapter and use manual generic file mappings (archive paths as-is).
