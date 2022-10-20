//! rvps

use std::{
    net::SocketAddr,
    sync::{Arc, Mutex},
};

use anyhow::*;
use clap::Parser;
use reference_value_provider_service as rvps;

mod grpc;

#[derive(Parser, Debug)]
#[clap(author, version, about, long_about = None)]
struct Args {
    #[clap(short, long, value_parser)]
    control_addr: String,

    #[clap(short, long, value_parser)]
    ac_addr: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    println!("control addr: {}", args.control_addr);
    println!("ac addr: {}", args.ac_addr);

    let control_addr = args.control_addr.parse::<SocketAddr>()?;

    let ac_addr = args.ac_addr.parse::<SocketAddr>()?;
    let rvps = Arc::new(Mutex::new(rvps::Core::new(
        rvps::cache::simple::SimpleCache::new(),
    )));

    let control_server = grpc::control::start_service(control_addr, rvps.clone());
    let ac_server = grpc::query::start_service(ac_addr, rvps.clone());

    tokio::join!(control_server, ac_server).0
}
