agent {
    data_dir = "/var/lib/spire/agent/.data"
    log_level = "DEBUG"
    server_address = "spire-server"
    server_port = "8081"
    socket_path = "/run/spire/agent/public/api.sock"
    trust_bundle_path = "/opt/spire/bootstrap/bootstrap-trust-bundle.pem"
    trust_domain = "spire.hashibank.demo"
    join_token = "__JOIN_TOKEN__"
}

plugins {
    NodeAttestor "join_token" {
        plugin_data {
        }
    }

    KeyManager "disk" {
        plugin_data {
            directory = "/var/lib/spire/agent/.data"
        }
    }

    WorkloadAttestor "docker" {
        plugin_data {
        }
    }
}
