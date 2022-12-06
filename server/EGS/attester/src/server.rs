//! Server

use anyhow::*;
use tonic::transport::Server as TServer;
use attestation::remote_attestation_server::{RemoteAttestationServer, RemoteAttestation};
use attestation::{RemoteAttestationReq, RemoteAttestationRes};
use tokio::fs;

use lib::get_evidence;

use std::net::SocketAddr;

pub const REPORT_DATA: &[u8] = "12345678123456781234567812345678".as_bytes();

pub const 

pub mod attestation {
    tonic::include_proto!("attestation");
}

pub struct Server {}

#[tonic::async_trait]
impl RemoteAttestation for Server {
    async fn get_attestation_evidence(
        &self,
        _request: tonic::Request<RemoteAttestationReq>,
    ) -> Result<tonic::Response<RemoteAttestationRes>, tonic::Status> {
        let report_data = REPORT_DATA.to_vec();
        let ev = get_evidence(report_data)
            .await
            .map_err(|e| tonic::Status::internal(format!("get evidence failed: {}", e.to_string())))?;

        fs::write("../../fixtures/quote", &ev.quote)
            .await
            .map_err(|e| tonic::Status::internal(format!("write quote failed: {}", e.to_string())))?;
        fs::write("../../fixtures/eventlog_info", &ev.eventlog_info)
            .await
            .map_err(|e| tonic::Status::internal(format!("write eventlog_info failed: {}", e.to_string())))?;
        fs::write("../../fixtures/eventlog_data", &ev.eventlog_data)
            .await
            .map_err(|e| tonic::Status::internal(format!("write eventlog_data failed: {}", e.to_string())))?;

        let evidence = serde_json::to_string(&ev)
            .map_err(|e| tonic::Status::internal(format!("serialize evidence failed: {}", e.to_string())))?;

        let res = RemoteAttestationRes {
            evidence,
        };

        Result::Ok(tonic::Response::new(res))
    }
}

pub async fn start_service(socket: SocketAddr) -> Result<()> {
    let service = Server {};

    let _server = TServer::builder()
        .add_service(RemoteAttestationServer::new(service))
        .serve(socket)
        .await?;
    Ok(())
}