// Acceptance-only cleanup through the same native credential backend and
// canonical account naming used by the runtime. Never print credential values.
#[test]
#[ignore = "requires an explicitly marked isolated credential fixture"]
fn native_fixture_secret_key() {
    use codex_keyring_store::{DefaultKeyringStore, KeyringStore};
    use codex_secrets::{LocalSecretsNamespace, compute_keyring_account};
    let home = std::path::PathBuf::from(
        std::env::var_os("CODEX_DEEPSEEK_KEYRING_FIXTURE_HOME").expect("fixture home"),
    ).canonicalize().unwrap();
    assert_eq!(
        std::fs::read_to_string(home.join(".credential-acceptance-fixture")).unwrap(),
        "NONFUNCTIONAL_TEST_CREDENTIALS_ONLY",
    );
    let account = compute_keyring_account(&home, LocalSecretsNamespace::CodexAuth);
    let store = DefaultKeyringStore;
    let present_before = store.load("codex", &account).unwrap().is_some();
    let deleted = store.delete("codex", &account).unwrap();
    let present_after = store.load("codex", &account).unwrap().is_some();
    println!("KEYRING_FIXTURE_RESULT {}", serde_json::json!({
        "present_before": present_before, "deleted": deleted,
        "present_after": present_after, "account": account,
    }));
    assert!(!present_after);
}
