use anyhow::*;
use rvps::cache::Cache;
use rvps::{Core, RVPSAPI};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tonic::{transport::Server, Response};

use reference_value_provider_service as rvps;

use control::process_provenance_server::{ProcessProvenance, ProcessProvenanceServer};
use control::{ProvenanceReq, ProvenanceRes};

pub mod control {
    tonic::include_proto!("control");
}

pub struct Control<T: Cache> {
    rvps: Arc<Mutex<rvps::Core<T>>>,
}

#[tonic::async_trait]
impl<'a, T> ProcessProvenance for Control<T>
where
    T: Cache + 'static,
    Arc<Mutex<Core<T>>>: Sync + Send,
{
    async fn registry(
        &self,
        request: tonic::Request<ProvenanceReq>,
    ) -> Result<tonic::Response<ProvenanceRes>, tonic::Status> {
        let request = request.into_inner();

        {
            let mut rvps = self.rvps.lock().unwrap();
            let message: rvps::Message = serde_json::from_str(&request.message).map_err(|e| {
                tonic::Status::internal(format!("failed to parse message: {}", e.to_string()))
            })?;

            rvps.verify_and_extract(message).map_err(|e| {
                tonic::Status::internal(format!("verify failed: {}", e.to_string()))
            })?;
        }

        let reply = ProvenanceRes {
            result: "Ok".into(),
        };
        Result::Ok(Response::new(reply))
    }
}

pub async fn start_service<T>(socket: SocketAddr, rvps: Arc<Mutex<rvps::Core<T>>>) -> Result<()>
where
    T: Cache + 'static,
    Arc<Mutex<Core<T>>>: Sync + Send,
{
    let service = Control { rvps };

    let _server = Server::builder()
        .add_service(ProcessProvenanceServer::new(service))
        .serve(socket)
        .await?;
    Ok(())
}
