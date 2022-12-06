//! Attestation-Client for Guanfu

use anyhow::*;
use attestation::remote_attestation_client::RemoteAttestationClient;
use attestation::RemoteAttestationReq;
use clap::Parser;
use query::query_reference_value_client::QueryReferenceValueClient;
use query::QueryReq;
use reference_value_provider_service::ReferenceValue;
use tokio::fs;
use tracing::{error, info};
use tracing_subscriber::{
    fmt::layer, prelude::__tracing_subscriber_SubscriberExt, util::SubscriberInitExt, EnvFilter,
};

pub mod query {
    tonic::include_proto!("query");
}

pub mod attestation {
    tonic::include_proto!("attestation");
}

mod verifier;

const ARTIFACT_NAME: &str = "";

#[derive(Parser, Debug)]
#[clap(author, version, about, long_about = None)]
struct Args {
    #[clap(short, long, value_parser)]
    rvps_addr: String,

    #[clap(short, long, value_parser)]
    as_addr: String,

    #[clap(short, long)]
    verbose: bool,
}

async fn real_main() -> Result<()> {
    let args = Args::parse();

    // setup logging
    let level_filter = if args.verbose { "debug" } else { "info" };
    let filter_layer = EnvFilter::new(level_filter);
    tracing_subscriber::registry()
        .with(filter_layer)
        .with(layer().with_writer(std::io::stderr))
        .init();

    info!(rvps_addr =? args.rvps_addr);
    info!(as_addr =? args.as_addr);

    let as_addr = args.as_addr;
    let rvps_addr = args.rvps_addr;

    // get evidences
    let mut as_client = RemoteAttestationClient::connect(as_addr).await;
    println!("{:?}", as_client);

    let mut as_client = as_client.unwrap();
    let query = RemoteAttestationReq {};
    let evi = as_client
        .get_attestation_evidence(query)
        .await?
        .into_inner()
        .status;
    info!("get evidence done.");

    // for debug
    // println!("{}", evi);
    // fs::write("./evi", &evi)
    //         .await
    //         .map_err(|e| tonic::Status::internal(format!("write evi failed: {}", e.to_string())))?;

    let evi = serde_json::from_str(&evi)?;
    let event_log = verifier::verify_evidence(verifier::TEE, evi, verifier::REPORT_DATA).await?;
    info!("verify evidence done.");

    // get reference values
    let mut rv_client = QueryReferenceValueClient::connect(rvps_addr).await?;
    let name = String::from(ARTIFACT_NAME);
    let query = QueryReq { name };
    let rv = match rv_client.query(query).await?.into_inner().reference_value {
        None => bail!("No reference value find."),
        Some(r) => serde_json::from_str::<ReferenceValue>(&r)?,
    };
    info!("get reference values done.");

    verifier::verify(event_log, rv)?;
    // compare
    info!("compare reference values done.");

    Ok(())
}

#[tokio::main]
async fn main() {
    match real_main().await {
        std::result::Result::Ok(_) => info!("attestation succeed!"),
        Err(e) => error!("attestation failed: {}", e.to_string()),
    }
}
