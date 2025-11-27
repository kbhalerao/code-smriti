/**
 * Couchbase Database Client
 *
 * Provides connection management and typed access to Couchbase buckets.
 * All database operations should go through this module.
 */

import { Cluster, Collection, Bucket } from 'couchbase';
import {
  COUCHBASE_HOST,
  COUCHBASE_USER,
  COUCHBASE_PASSWORD,
  COUCHBASE_BUCKET_CODE,
  COUCHBASE_BUCKET_USERS,
  COUCHBASE_BUCKET_JOBS
} from '$env/static/private';

let cluster: Cluster | null = null;
let codeBucket: Bucket | null = null;
let usersBucket: Bucket | null = null;
let jobsBucket: Bucket | null = null;

/**
 * Initialize Couchbase cluster connection
 */
async function initCluster(): Promise<Cluster> {
  if (cluster) return cluster;

  const connectionString = `couchbase://${COUCHBASE_HOST}`;

  cluster = await Cluster.connect(connectionString, {
    username: COUCHBASE_USER,
    password: COUCHBASE_PASSWORD,
  });

  // Wait for cluster to be ready
  await cluster.waitUntilReady();

  return cluster;
}

/**
 * Get Couchbase cluster instance
 */
export async function getCluster(): Promise<Cluster> {
  if (!cluster) {
    await initCluster();
  }
  return cluster!;
}

/**
 * Get code_kosha bucket (multi-tenant code chunks)
 */
export async function getCodeBucket(): Promise<Bucket> {
  if (!codeBucket) {
    const cl = await getCluster();
    codeBucket = cl.bucket(COUCHBASE_BUCKET_CODE);
  }
  return codeBucket;
}

/**
 * Get users bucket (credentials and GitHub PATs)
 */
export async function getUsersBucket(): Promise<Bucket> {
  if (!usersBucket) {
    const cl = await getCluster();
    usersBucket = cl.bucket(COUCHBASE_BUCKET_USERS);
  }
  return usersBucket;
}

/**
 * Get ingestion_jobs bucket (job queue and progress)
 */
export async function getJobsBucket(): Promise<Bucket> {
  if (!jobsBucket) {
    const cl = await getCluster();
    jobsBucket = cl.bucket(COUCHBASE_BUCKET_JOBS);
  }
  return jobsBucket;
}

/**
 * Get default collection from code_kosha bucket
 */
export async function getCodeCollection(): Promise<Collection> {
  const bucket = await getCodeBucket();
  return bucket.defaultCollection();
}

/**
 * Get default collection from users bucket
 */
export async function getUsersCollection(): Promise<Collection> {
  const bucket = await getUsersBucket();
  return bucket.defaultCollection();
}

/**
 * Get default collection from ingestion_jobs bucket
 */
export async function getJobsCollection(): Promise<Collection> {
  const bucket = await getJobsBucket();
  return bucket.defaultCollection();
}

/**
 * Close all connections (for graceful shutdown)
 */
export async function closeConnections(): Promise<void> {
  if (cluster) {
    await cluster.close();
    cluster = null;
    codeBucket = null;
    usersBucket = null;
    jobsBucket = null;
  }
}
