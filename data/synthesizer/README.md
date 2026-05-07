# Synthetic HVAC Home-Services Data Generator

Generates the ADR-008 twelve-table HVAC dataset (customer, technician, equipment, service_job, customer_signal_daily, equipment_telemetry_daily, technician_utilization_daily, dispatch_event, parts_inventory, review, truck_roll, warranty_claim) as Parquet, lands them in S3, and registers them as Iceberg in Glue. All PII is synthetic.

See ADR-008 for the schema decision and `scripts/seed-data.sh` for the end-to-end pipeline.
