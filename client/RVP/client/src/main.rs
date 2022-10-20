//! rvps's client

use anyhow::*;
use clap::Parser;

use control::process_provenance_client::ProcessProvenanceClient;
use control::ProvenanceReq;

pub mod control {
    tonic::include_proto!("control");
}

#[derive(Parser, Debug)]
#[clap(author, version, about, long_about = None)]
struct Args {
    #[clap(short, long, value_parser)]
    rvps_addr: String,

    #[clap(short, long, value_parser)]
    path: String,
}

async fn real_main() -> Result<String> {
    let args = Args::parse();

    println!("rvps addr: {}", args.rvps_addr);
    println!("read provenance message from: {}", args.path);

    let message = tokio::fs::read(args.path).await?;

    let req = ProvenanceReq {
        message: String::from_utf8(message)?,
    };

    let mut client = ProcessProvenanceClient::connect(args.rvps_addr).await?;
    let res = client.registry(req).await?;
    let res = res.into_inner();
    Ok(res.result)
}

#[tokio::main]
async fn main() {
    match real_main().await {
        std::result::Result::Ok(res) => println!("Send message succeed: {}", res),
        Err(e) => println!("Send message failed: {}", e),
    }
}
