// Exercise the native HTTP policy on every host target, including Windows.
// The Python verifier owns the loopback gateway and observable upstream socket.
#[test]
#[ignore = "requires the isolated Python gateway/upstream fixture"]
fn native_policy_revocation_propagates_to_upstream() {
    use codex_http_client::{
        ClientRouteClass, DestinationPolicy, HttpClientFactory, NetworkPolicyController,
        OutboundProxyPolicy,
    };
    let url = std::env::var("CODEX_DEEPSEEK_E2E_GATEWAY").expect("isolated fixture endpoint");
    let token = std::env::var("CODEX_DEEPSEEK_E2E_TOKEN").expect("isolated fixture token");
    tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        let controller = NetworkPolicyController::default();
        let policy = controller.policy();
        let revision = policy.revision();
        assert!(controller.publish(revision, DestinationPolicy::Restricted {
            allowed_hosts: ["openrouter.ai".to_owned()].into(),
        }));
        let factory = HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault)
            .with_network_policy(policy);
        let client = factory.build_client(&url, ClientRouteClass::Api).unwrap();
        let mut response = client.post(&url).bearer_auth(token)
            .json(&serde_json::json!({
                "model": "deepseek/deepseek-v4.1-flash-1017",
                "input": "isolated native policy cancellation fixture",
                "stream": true, "store": false,
            }))
            .send().await.unwrap();
        assert_eq!(response.status(), http::StatusCode::OK);
        assert!(response.chunk().await.unwrap().is_some());
        assert!(controller.publish(revision, DestinationPolicy::Restricted {
            allowed_hosts: ["denied.invalid".to_owned()].into(),
        }));
        assert!(response.chunk().await.is_err());
    });
}
