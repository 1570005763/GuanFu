use anyhow::*;
use rvps::cache::Cache;
use rvps::{Core, RVPSAPI};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use tonic::{transport::Server, Response};

use reference_value_provider_service as rvps;

use control::query_reference_value_server::{QueryReferenceValue, QueryReferenceValueServer};
use control::{QueryReq, QueryRes};

pub mod control {
    tonic::include_proto!("query");
}

pub struct Query<T: Cache> {
    rvps: Arc<Mutex<rvps::Core<T>>>,
}

#[tonic::async_trait]
impl<'a, T> QueryReferenceValue for Query<T>
where
    T: Cache + 'static,
    Arc<Mutex<Core<T>>>: Sync + Send,
{
    async fn query(
        &self,
        request: tonic::Request<QueryReq>,
    ) -> Result<tonic::Response<QueryRes>, tonic::Status> {
        let request = request.into_inner();

        let rv = {
            let rvps = self.rvps.lock().unwrap();
            let name = &request.name;

            rvps.get_rv(name).map_err(|e| {
                tonic::Status::internal(format!("Get reference value failed: {}", e.to_string()))
            })?
        };

        let rv = match rv {
            Some(rv) => Some(serde_json::to_string(&rv).map_err(|e| {
                tonic::Status::internal(format!("Reference value serde failed: {}", e.to_string()))
            })?),
            None => None,
        };
        let reply = QueryRes {
            result: "Ok".into(),
            reference_value: rv,
        };
        Result::Ok(Response::new(reply))
    }
}

pub async fn start_service<T>(socket: SocketAddr, rvps: Arc<Mutex<rvps::Core<T>>>) -> Result<()>
where
    T: Cache + 'static,
    Arc<Mutex<Core<T>>>: Sync + Send,
{
    let service = Query { rvps };

    let _server = Server::builder()
        .add_service(QueryReferenceValueServer::new(service))
        .serve(socket)
        .await?;
    Ok(())
}
