use anyhow::Result;
use log::info;
use clap::Parser;

use std::net::SocketAddr;

use crate::server::start_service;

mod server;

#[derive(Parser, Debug)]
#[clap(author, version, about, long_about = None)]
struct Args {
    #[clap(short, long, value_parser)]
    ac_addr: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let args = Args::parse();

    let ac_addr = args.ac_addr
        .parse::<SocketAddr>()?;

    info!("Listen to {}", ac_addr);

    start_service(ac_addr).await
}
