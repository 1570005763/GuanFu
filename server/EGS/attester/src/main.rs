use anyhow::Result;
use clap::Parser;
use tracing::{error, info};
use tracing_subscriber::{
    fmt::layer, prelude::__tracing_subscriber_SubscriberExt, util::SubscriberInitExt, EnvFilter,
};

use std::net::SocketAddr;

use crate::server::start_service;

mod server;

#[derive(Parser, Debug)]
#[clap(author, version, about, long_about = None)]
struct Args {
    #[clap(short, long, value_parser)]
    ac_addr: String,

    #[clap(short, long)]
    verbose: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    env_logger::init();
    let args = Args::parse();

    // setup logging
    let level_filter = if args.verbose { "debug" } else { "info" };
    let filter_layer = EnvFilter::new(level_filter);
    tracing_subscriber::registry()
        .with(filter_layer)
        .with(fmt::layer().with_writer(std::io::stderr))
        .with(layer().with_writer(std::io::stderr))
        .init();

    let ac_addr = args.ac_addr
        .parse::<SocketAddr>()?;

    info!("Listen to {}", ac_addr);

    start_service(ac_addr).await
}
