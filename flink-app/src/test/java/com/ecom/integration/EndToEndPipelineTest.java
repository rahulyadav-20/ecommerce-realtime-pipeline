package com.ecom.integration;

import com.ecom.events.ClickstreamEvent;
import com.ecom.events.EventType;
import com.ecom.operators.DedupFunction;
import com.ecom.operators.ValidateAndClean;
import io.confluent.kafka.serializers.KafkaAvroDeserializer;
import io.confluent.kafka.serializers.KafkaAvroSerializer;
import org.apache.avro.specific.SpecificRecord;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.restartstrategy.RestartStrategies;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroDeserializationSchema;
import org.apache.flink.formats.avro.registry.confluent.ConfluentRegistryAvroSerializationSchema;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.OutputTag;
import org.apache.kafka.clients.admin.AdminClient;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.awaitility.Awaitility;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.Timeout;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.KafkaContainer;
import org.testcontainers.containers.Network;
import org.testcontainers.containers.wait.strategy.Wait;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;

import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * End-to-end integration test for the Flink ETL pipeline.
 *
 * <h2>What is tested</h2>
 * <ol>
 *   <li>1 000 unique valid events → raw topic</li>
 *   <li>100 duplicate events (reusing existing event_ids) → raw topic</li>
 *   <li>Flink job runs: ValidateAndClean → DedupFunction → clean topic</li>
 *   <li>Exactly 1 000 events appear in the clean topic (100 deduped)</li>
 *   <li>No duplicate event_ids in the clean topic</li>
 *   <li>DLQ topic receives 0 messages (all events pass validation)</li>
 *   <li>Flink job remains in RUNNING state (checkpoint succeeded)</li>
 * </ol>
 *
 * <h2>Infrastructure</h2>
 * <ul>
 *   <li>Kafka 7.6.1 — Testcontainers {@link KafkaContainer}</li>
 *   <li>Schema Registry 7.6.1 — Testcontainers {@link GenericContainer}</li>
 *   <li>Flink — {@link StreamExecutionEnvironment#createLocalEnvironment()}</li>
 * </ul>
 *
 * <h2>Performance note</h2>
 * First run downloads ~800 MB of Docker images.  Subsequent runs use the local
 * image cache and complete in ~60 s.  Tag the class with {@code @Tag("integration")}
 * and use {@code mvn test -Dgroups=integration} to run selectively.
 *
 * <pre>
 *   mvn test -pl flink-app -Dtest=EndToEndPipelineTest
 * </pre>
 */
@Tag("integration")
@Testcontainers
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
@Timeout(value = 5, unit = TimeUnit.MINUTES)
class EndToEndPipelineTest {

    private static final Logger LOG = LoggerFactory.getLogger(EndToEndPipelineTest.class);

    // ── Test constants ────────────────────────────────────────────────────────

    static final int UNIQUE_EVENT_COUNT    = 1_000;
    static final int DUPLICATE_EVENT_COUNT = 100;    // re-uses first 100 event IDs
    static final int TOTAL_PRODUCED        = UNIQUE_EVENT_COUNT + DUPLICATE_EVENT_COUNT;

    static final String RAW_TOPIC   = "ecommerce.events.raw.v1";
    static final String CLEAN_TOPIC = "ecommerce.events.clean.v1";
    static final String DLQ_TOPIC   = "dlq.events";

    static final Duration POLL_TIMEOUT      = Duration.ofSeconds(90);
    static final Duration CONSUMER_POLL     = Duration.ofMillis(500);

    /** Checkpoint dir inside the test JVM's temp directory. */
    static final String CHECKPOINT_DIR =
            "file://" + System.getProperty("java.io.tmpdir").replace('\\', '/')
            + "/flink-ckpt-e2e-test/";

    // ── Docker network (Kafka ↔ Schema Registry need to talk to each other) ──

    static final Network DOCKER_NETWORK = Network.newNetwork();

    // ── Containers ────────────────────────────────────────────────────────────

    /**
     * Kafka broker.  KafkaContainer wraps cp-kafka and automatically configures
     * the PLAINTEXT / BROKER listeners needed for internal + external access.
     */
    @Container
    static final KafkaContainer KAFKA = new KafkaContainer(
            DockerImageName.parse("confluentinc/cp-kafka:7.6.1"))
            .withNetwork(DOCKER_NETWORK)
            .withNetworkAliases("kafka")
            .withReuse(false);

    /**
     * Confluent Schema Registry.  Must start after Kafka.
     *
     * <p>SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS uses the Docker-internal
     * broker hostname ("kafka:9092") — not the host-mapped port.
     */
    @Container
    static final GenericContainer<?> SCHEMA_REGISTRY = new GenericContainer<>(
            DockerImageName.parse("confluentinc/cp-schema-registry:7.6.1"))
            .withNetwork(DOCKER_NETWORK)
            .withNetworkAliases("schema-registry")
            .withEnv("SCHEMA_REGISTRY_HOST_NAME",                     "schema-registry")
            .withEnv("SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS",  "kafka:9092")
            .withEnv("SCHEMA_REGISTRY_KAFKASTORE_TIMEOUT_MS",         "60000")
            .withEnv("SCHEMA_REGISTRY_LISTENERS",                     "http://0.0.0.0:8081")
            .withExposedPorts(8081)
            .waitingFor(Wait.forHttp("/subjects").forStatusCode(200)
                    .withStartupTimeout(Duration.ofMinutes(2)))
            .dependsOn(KAFKA);

    // ── Shared test state (populated in @BeforeAll, read in @Test) ────────────

    static String bootstrapServers;
    static String schemaRegistryUrl;

    /** All event_ids that were sent to the raw topic (only the unique ones). */
    static final Set<String> producedUniqueIds = new LinkedHashSet<>();

    /** All event_ids consumed from the clean topic. */
    static final List<String> consumedCleanIds = new ArrayList<>();

    /** Number of messages consumed from the DLQ topic. */
    static final AtomicInteger dlqMessageCount = new AtomicInteger(0);

    /** Flink job client — used to check job status and cancel in @AfterAll. */
    static volatile org.apache.flink.core.execution.JobClient jobClient;

    /** Flag set when Awaitility successfully drains the clean topic. */
    static final AtomicBoolean cleanTopicDrained = new AtomicBoolean(false);

    // ═════════════════════════════════════════════════════════════════════════
    // SETUP
    // ═════════════════════════════════════════════════════════════════════════

    @BeforeAll
    static void setupAll() throws Exception {
        bootstrapServers  = KAFKA.getBootstrapServers();
        schemaRegistryUrl = "http://" + SCHEMA_REGISTRY.getHost()
                          + ":" + SCHEMA_REGISTRY.getMappedPort(8081);

        LOG.info("=== EndToEndPipelineTest setup ===");
        LOG.info("Kafka         : {}", bootstrapServers);
        LOG.info("Schema Reg    : {}", schemaRegistryUrl);
        LOG.info("Checkpoint dir: {}", CHECKPOINT_DIR);

        // Steps must run in this exact order
        step1_createKafkaTopics();
        step2_submitFlinkJob();
        step3_produceEvents();
        step4_drainCleanTopic();
        step5_drainDlqTopic();
    }

    // ── Step 1 — Create topics ────────────────────────────────────────────────

    private static void step1_createKafkaTopics() throws Exception {
        LOG.info("Step 1: Creating Kafka topics...");
        Properties adminProps = new Properties();
        adminProps.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);

        try (AdminClient admin = AdminClient.create(adminProps)) {
            List<NewTopic> topics = List.of(
                    new NewTopic(RAW_TOPIC,   4, (short) 1),
                    new NewTopic(CLEAN_TOPIC, 4, (short) 1),
                    new NewTopic(DLQ_TOPIC,   1, (short) 1)
            );
            admin.createTopics(topics).all().get(30, TimeUnit.SECONDS);
        }
        LOG.info("Step 1: Topics created — {}, {}, {}", RAW_TOPIC, CLEAN_TOPIC, DLQ_TOPIC);
    }

    // ── Step 2 — Submit Flink job ─────────────────────────────────────────────

    /**
     * Builds the same topology as {@code EcomEtlJob} but parameterised with
     * test-container endpoints and a shorter checkpoint interval.
     *
     * <p>We do NOT submit a fat JAR here — the class is on the test classpath,
     * so {@link StreamExecutionEnvironment#createLocalEnvironment()} picks it up
     * without file-system I/O.
     *
     * <p>The job is submitted asynchronously via {@link StreamExecutionEnvironment#executeAsync}
     * so the test thread can continue producing events.
     */
    private static void step2_submitFlinkJob() throws Exception {
        LOG.info("Step 2: Submitting Flink ETL job...");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.createLocalEnvironment();
        env.setParallelism(2);
        env.setRestartStrategy(RestartStrategies.noRestart());   // fail fast in tests

        // ── Checkpointing ────────────────────────────────────────────────────
        // 5 s interval (production uses 30 s) so the test sees a committed
        // checkpoint within the Awaitility window.
        env.enableCheckpointing(5_000, CheckpointingMode.EXACTLY_ONCE);
        env.getCheckpointConfig().setCheckpointStorage(CHECKPOINT_DIR);
        env.getCheckpointConfig().setCheckpointTimeout(60_000);
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);

        // ── Source ───────────────────────────────────────────────────────────
        KafkaSource<ClickstreamEvent> source = KafkaSource.<ClickstreamEvent>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics(RAW_TOPIC)
                .setGroupId("flink-e2e-test")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(
                        ConfluentRegistryAvroDeserializationSchema.forSpecific(
                                ClickstreamEvent.class, schemaRegistryUrl))
                .build();

        // ── ValidateAndClean ─────────────────────────────────────────────────
        OutputTag<String> dlqTag = new OutputTag<>("dlq") {};
        SingleOutputStreamOperator<ClickstreamEvent> cleaned = env
                .fromSource(source, WatermarkStrategy.noWatermarks(), "src-kafka-raw")
                .process(new ValidateAndClean(dlqTag))
                .name("op:validate-clean");

        // ── DedupFunction (1 h window is sufficient for test events) ─────────
        SingleOutputStreamOperator<ClickstreamEvent> deduped = cleaned
                .keyBy(e -> e.getEventId().toString())
                .process(new DedupFunction(1))
                .name("op:dedup");

        // ── Sink: clean hub topic ─────────────────────────────────────────────
        Properties txProps = new Properties();
        txProps.setProperty("transaction.timeout.ms", "60000");

        deduped.sinkTo(
                KafkaSink.<ClickstreamEvent>builder()
                        .setBootstrapServers(bootstrapServers)
                        .setKafkaProducerConfig(txProps)
                        .setRecordSerializer(
                                KafkaRecordSerializationSchema.<ClickstreamEvent>builder()
                                        .setTopic(CLEAN_TOPIC)
                                        .setValueSerializationSchema(
                                                ConfluentRegistryAvroSerializationSchema.forSpecific(
                                                        ClickstreamEvent.class,
                                                        CLEAN_TOPIC + "-value",
                                                        schemaRegistryUrl))
                                        .build())
                        .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                        .setTransactionalIdPrefix("e2e-test-clean-")
                        .build())
                .name("sink:kafka-clean");

        // ── Sink: DLQ ─────────────────────────────────────────────────────────
        cleaned.getSideOutput(dlqTag)
                .sinkTo(
                        KafkaSink.<String>builder()
                                .setBootstrapServers(bootstrapServers)
                                .setRecordSerializer(
                                        KafkaRecordSerializationSchema.<String>builder()
                                                .setTopic(DLQ_TOPIC)
                                                .setValueSerializationSchema(new SimpleStringSchema())
                                                .build())
                                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                                .build())
                .name("sink:kafka-dlq");

        // ── Execute asynchronously ────────────────────────────────────────────
        jobClient = env.executeAsync("e2e-integration-test-etl");
        LOG.info("Step 2: Flink job submitted  jobId={}", jobClient.getJobID());
    }

    // ── Step 3 — Produce events ───────────────────────────────────────────────

    /**
     * Produces {@value #UNIQUE_EVENT_COUNT} unique events followed by
     * {@value #DUPLICATE_EVENT_COUNT} duplicate events (reusing the first 100
     * event_ids).
     *
     * <p>Messages are serialised using {@link KafkaAvroSerializer}, which
     * prepends the Confluent magic byte + schema ID so Flink's
     * {@code ConfluentRegistryAvroDeserializationSchema} can decode them.
     */
    private static void step3_produceEvents() {
        LOG.info("Step 3: Producing {} unique + {} duplicate events...",
                UNIQUE_EVENT_COUNT, DUPLICATE_EVENT_COUNT);

        Map<String, Object> producerConf = new HashMap<>();
        producerConf.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG,    bootstrapServers);
        producerConf.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG,  StringSerializer.class.getName());
        producerConf.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG,KafkaAvroSerializer.class.getName());
        producerConf.put(ProducerConfig.ACKS_CONFIG,                  "all");
        producerConf.put(ProducerConfig.LINGER_MS_CONFIG,             "10");
        producerConf.put("schema.registry.url",                       schemaRegistryUrl);
        producerConf.put("specific.avro.reader",                      true);

        try (KafkaProducer<String, SpecificRecord> producer = new KafkaProducer<>(producerConf)) {

            // 1 000 unique events
            List<String> allIds = new ArrayList<>(UNIQUE_EVENT_COUNT);
            for (int i = 0; i < UNIQUE_EVENT_COUNT; i++) {
                ClickstreamEvent event = buildValidEvent("evt-" + i);
                allIds.add(event.getEventId().toString());
                producedUniqueIds.add(event.getEventId().toString());
                producer.send(new ProducerRecord<>(RAW_TOPIC,
                        event.getEventId().toString(), event));
            }
            producer.flush();
            LOG.info("Step 3: {} unique events sent", UNIQUE_EVENT_COUNT);

            // 100 duplicate events — first 100 IDs re-sent with a new event
            // but the SAME event_id so DedupFunction should filter them out.
            for (int i = 0; i < DUPLICATE_EVENT_COUNT; i++) {
                ClickstreamEvent dup = buildValidEvent(allIds.get(i));   // same event_id
                producer.send(new ProducerRecord<>(RAW_TOPIC,
                        dup.getEventId().toString(), dup));
            }
            producer.flush();
            LOG.info("Step 3: {} duplicate events sent (total={})",
                    DUPLICATE_EVENT_COUNT, TOTAL_PRODUCED);
        }
    }

    // ── Step 4 — Wait for clean topic to reach UNIQUE_EVENT_COUNT ────────────

    /**
     * Polls the clean topic using Awaitility until exactly
     * {@value #UNIQUE_EVENT_COUNT} events are present.
     *
     * <p>The consumer uses {@code isolation.level=read_committed} so that
     * transactional messages from the {@code EXACTLY_ONCE} KafkaSink are only
     * visible after the Flink checkpoint commits the transaction.
     */
    private static void step4_drainCleanTopic() {
        LOG.info("Step 4: Waiting for {} messages in clean topic (timeout {}s)...",
                UNIQUE_EVENT_COUNT, POLL_TIMEOUT.getSeconds());

        Properties consumerProps = new Properties();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG,   bootstrapServers);
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG,            "e2e-test-clean-consumer");
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG,   "earliest");
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,   StringDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, KafkaAvroDeserializer.class.getName());
        consumerProps.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG,  "false");
        // read_committed ensures only checkpoint-committed messages are visible
        consumerProps.put(ConsumerConfig.ISOLATION_LEVEL_CONFIG,     "read_committed");
        consumerProps.put("schema.registry.url",                     schemaRegistryUrl);
        consumerProps.put("specific.avro.reader",                    true);

        try (KafkaConsumer<String, ClickstreamEvent> consumer = new KafkaConsumer<>(consumerProps)) {
            consumer.subscribe(List.of(CLEAN_TOPIC));

            Awaitility.await()
                    .alias("clean topic reaches " + UNIQUE_EVENT_COUNT + " messages")
                    .atMost(POLL_TIMEOUT)
                    .pollInterval(Duration.ofSeconds(2))
                    .untilAsserted(() -> {
                        ConsumerRecords<String, ClickstreamEvent> records =
                                consumer.poll(CONSUMER_POLL);
                        records.forEach(r -> consumedCleanIds.add(r.key()));
                        assertThat(consumedCleanIds)
                                .as("clean topic should accumulate %d events", UNIQUE_EVENT_COUNT)
                                .hasSize(UNIQUE_EVENT_COUNT);
                    });

            cleanTopicDrained.set(true);
        }
        LOG.info("Step 4: Clean topic drained — {} events consumed", consumedCleanIds.size());
    }

    // ── Step 5 — Quick DLQ check ──────────────────────────────────────────────

    /**
     * Reads the DLQ topic for 5 seconds.  Any message received increments the
     * {@link #dlqMessageCount} counter.
     *
     * <p>Because all test events are valid, this count should remain 0.
     */
    private static void step5_drainDlqTopic() {
        LOG.info("Step 5: Checking DLQ topic (polling for 5 s)...");

        Properties dlqProps = new Properties();
        dlqProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG,  bootstrapServers);
        dlqProps.put(ConsumerConfig.GROUP_ID_CONFIG,           "e2e-test-dlq-consumer");
        dlqProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG,  "earliest");
        dlqProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,   StringDeserializer.class.getName());
        dlqProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        dlqProps.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");

        try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(dlqProps)) {
            consumer.subscribe(List.of(DLQ_TOPIC));
            // Poll twice — 2.5 s each — to give any late DLQ messages time to arrive
            for (int i = 0; i < 2; i++) {
                ConsumerRecords<String, String> dlqRecords = consumer.poll(Duration.ofMillis(2_500));
                dlqMessageCount.addAndGet(dlqRecords.count());
            }
        }
        LOG.info("Step 5: DLQ message count = {}", dlqMessageCount.get());
    }

    // ═════════════════════════════════════════════════════════════════════════
    // ASSERTIONS
    // ═════════════════════════════════════════════════════════════════════════

    @Test
    @Order(1)
    @DisplayName("T1 — Clean topic receives exactly UNIQUE_EVENT_COUNT messages")
    void cleanTopicReceivesAllUniqueEvents() {
        assertThat(cleanTopicDrained.get())
                .as("Awaitility must have successfully drained the clean topic")
                .isTrue();

        assertThat(consumedCleanIds)
                .as("Expected %d events in clean topic; got %d",
                        UNIQUE_EVENT_COUNT, consumedCleanIds.size())
                .hasSize(UNIQUE_EVENT_COUNT);
    }

    @Test
    @Order(2)
    @DisplayName("T2 — No duplicate event_ids in the clean topic")
    void noDuplicateEventIds() {
        Set<String> uniqueInClean = new HashSet<>(consumedCleanIds);

        assertThat(uniqueInClean)
                .as("Duplicate event_ids found in clean topic — DedupFunction not working")
                .hasSize(consumedCleanIds.size());

        // Every event_id in the clean topic must also be in the produced set
        assertThat(uniqueInClean)
                .as("Clean topic contains event_ids not produced by this test")
                .isSubsetOf(producedUniqueIds);
    }

    @Test
    @Order(3)
    @DisplayName("T3 — DLQ topic receives 0 messages (all events pass validation)")
    void dlqIsEmpty() {
        assertThat(dlqMessageCount.get())
                .as("DLQ should be empty — all %d events are valid", UNIQUE_EVENT_COUNT)
                .isZero();
    }

    @Test
    @Order(4)
    @DisplayName("T4 — Flink job is still RUNNING after processing all events")
    void flinkJobRemainsRunning() throws Exception {
        assertThat(jobClient)
                .as("JobClient must have been initialised in @BeforeAll")
                .isNotNull();

        org.apache.flink.api.common.JobStatus status =
                jobClient.getJobStatus().get(10, TimeUnit.SECONDS);

        assertThat(status)
                .as("Flink job should be RUNNING after processing %d events; was %s",
                        TOTAL_PRODUCED, status)
                .isEqualTo(org.apache.flink.api.common.JobStatus.RUNNING);
    }

    @Test
    @Order(5)
    @DisplayName("T5 — DedupFunction filters exactly DUPLICATE_EVENT_COUNT duplicates")
    void deduplicationFilteredCorrectNumberOfEvents() {
        // TOTAL_PRODUCED - UNIQUE_EVENT_COUNT = exactly the duplicates
        int expectedDeduped = TOTAL_PRODUCED - UNIQUE_EVENT_COUNT;

        // consumedCleanIds.size() == UNIQUE_EVENT_COUNT (verified in T1)
        // produced = TOTAL_PRODUCED  →  filtered = TOTAL_PRODUCED - cleanCount
        int actualFiltered = TOTAL_PRODUCED - consumedCleanIds.size();

        assertThat(actualFiltered)
                .as("DedupFunction should have filtered exactly %d duplicate events",
                        expectedDeduped)
                .isEqualTo(expectedDeduped);
    }

    @Test
    @Order(6)
    @DisplayName("T6 — All produced unique event_ids are present in the clean topic")
    void allProducedIdsReachCleanTopic() {
        Set<String> cleanSet = new HashSet<>(consumedCleanIds);

        // Every unique id we produced must appear in the clean topic
        List<String> missing = producedUniqueIds.stream()
                .filter(id -> !cleanSet.contains(id))
                .collect(Collectors.toList());

        assertThat(missing)
                .as("These event_ids were produced but never reached the clean topic: %s",
                        missing.stream().limit(10).collect(Collectors.joining(", ")))
                .isEmpty();
    }

    @Test
    @Order(7)
    @DisplayName("T7 — Checkpoint directory is non-empty (checkpoint completed)")
    void checkpointDirectoryContainsFiles() {
        // The Flink local environment writes checkpoint metadata to CHECKPOINT_DIR.
        // At least one checkpoint must complete within our test window.
        java.io.File ckptDir = new java.io.File(
                CHECKPOINT_DIR.replaceFirst("^file://", ""));

        assertThat(ckptDir)
                .as("Checkpoint dir must exist: %s", CHECKPOINT_DIR)
                .exists()
                .isDirectory();

        // Walk recursively; expect at least the chk-1/_metadata file
        long fileCount = java.util.Arrays.stream(
                Optional.ofNullable(ckptDir.listFiles()).orElse(new java.io.File[0]))
                .mapToLong(f -> f.isDirectory()
                        ? countFilesRecursively(f.toPath())
                        : 1L)
                .sum();

        assertThat(fileCount)
                .as("Checkpoint dir should contain at least one file " +
                    "(expected a completed checkpoint at interval=5s)")
                .isGreaterThanOrEqualTo(1L);
    }

    // ═════════════════════════════════════════════════════════════════════════
    // TEARDOWN
    // ═════════════════════════════════════════════════════════════════════════

    @AfterAll
    static void teardownAll() {
        LOG.info("=== EndToEndPipelineTest teardown ===");

        // Cancel the Flink job so it doesn't block test JVM exit
        if (jobClient != null) {
            try {
                jobClient.cancel().get(15, TimeUnit.SECONDS);
                LOG.info("Flink job cancelled");
            } catch (Exception e) {
                LOG.warn("Could not cancel Flink job cleanly: {}", e.getMessage());
            }
        }

        // Cleanup checkpoint directory (best effort)
        try {
            java.nio.file.Path ckptPath = java.nio.file.Path.of(
                    CHECKPOINT_DIR.replaceFirst("^file://", ""));
            if (java.nio.file.Files.exists(ckptPath)) {
                deleteDirectory(ckptPath);
                LOG.info("Checkpoint directory deleted: {}", ckptPath);
            }
        } catch (Exception e) {
            LOG.warn("Could not delete checkpoint dir: {}", e.getMessage());
        }
    }

    // ═════════════════════════════════════════════════════════════════════════
    // HELPERS
    // ═════════════════════════════════════════════════════════════════════════

    /**
     * Builds a valid {@link ClickstreamEvent} that passes all
     * {@link ValidateAndClean} checks.
     *
     * <p>If {@code eventId} is a pre-existing ID (for duplicates), the same
     * string is used so {@link DedupFunction} deduplicates the record.
     */
    private static ClickstreamEvent buildValidEvent(String eventId) {
        long now = Instant.now().toEpochMilli();
        return ClickstreamEvent.newBuilder()
                .setEventId(eventId)
                .setSessionId("session-" + UUID.randomUUID())
                .setEventTime(now)
                .setEventType(EventType.VIEW)
                .setDevice("web")
                .setIp("203.0.113." + (1 + Math.abs(eventId.hashCode()) % 254))
                .setUserId("user-" + (Math.abs(eventId.hashCode()) % 10_000))
                .setProductId("SKU-" + (Math.abs(eventId.hashCode()) % 1_000))
                .setCategory("electronics")
                .setQuantity(null)
                .setPrice(null)
                .setPaymentMode(null)
                .build();
    }

    /** Recursively count files under a directory path. */
    private static long countFilesRecursively(java.nio.file.Path dir) {
        try (var stream = java.nio.file.Files.walk(dir)) {
            return stream.filter(java.nio.file.Files::isRegularFile).count();
        } catch (java.io.IOException e) {
            return 0L;
        }
    }

    /** Best-effort recursive directory delete. */
    private static void deleteDirectory(java.nio.file.Path dir) throws java.io.IOException {
        try (var stream = java.nio.file.Files.walk(dir)) {
            stream.sorted(Comparator.reverseOrder())
                  .map(java.nio.file.Path::toFile)
                  .forEach(java.io.File::delete);
        }
    }
}
