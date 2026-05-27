ui = true
disable_mlock = true

api_addr = "https://hashibank-vault-perf-primary:8200"
cluster_addr = "https://hashibank-vault-perf-primary:8201"

listener "tcp" {
  address = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable = false
  tls_cert_file = "/vault/config/tls/hashibank-vault-perf-primary.crt"
  tls_key_file = "/vault/config/tls/hashibank-vault-perf-primary.key"
  tls_disable_client_certs = false
  tls_require_and_verify_client_cert = false
}

storage "raft" {
  path = "/vault/file"
  node_id = "hashibank-vault-perf-primary"
}
