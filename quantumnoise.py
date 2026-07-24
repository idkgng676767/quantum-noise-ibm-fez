"""
Quantum Noise Characterization - Data Collection Only
IBM Quantum Machine
Pulls REAL calibration data from backend properties. No fake/random numbers.
"""

import json
import numpy as np
from datetime import datetime
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_ibm_runtime import QiskitRuntimeService, EstimatorV2 as Estimator, EstimatorOptions
import time

print("=" * 80)
print("QUANTUM NOISE CHARACTERIZATION - DATA COLLECTION")
print("=" * 80)

# ============================================================================
# STEP 1: CONNECT TO QUANTUM HARDWARE
# ============================================================================
print("\n[1] Connecting to IBM Quantum hardware...")
service = QiskitRuntimeService()
backend = service.least_busy(simulator=False, operational=True, min_num_qubits=100)
print(f"✓ Connected to: {backend.name}")
print(f"  Qubits: {backend.num_qubits}")

# ============================================================================
# STEP 2: PULL REAL CALIBRATION DATA FROM BACKEND
# ============================================================================
# IBM backends publish live-measured error rates from their own daily
# calibration routines. This is real hardware data, not something we need
# to re-measure ourselves with toy circuits.
print("\n[2] Pulling real calibration data from backend...")

props = backend.properties()

single_qubit_errors = {}
readout_errors = {}
t1_times = {}
t2_times = {}

for q in range(backend.num_qubits):
    try:
        single_qubit_errors[q] = props.gate_error("sx", [q])
    except Exception:
        single_qubit_errors[q] = None
    try:
        readout_errors[q] = props.readout_error(q)
    except Exception:
        readout_errors[q] = None
    try:
        t1_times[q] = props.t1(q)
        t2_times[q] = props.t2(q)
    except Exception:
        t1_times[q] = None
        t2_times[q] = None

print(f"✓ Pulled single-qubit gate error, readout error, T1, T2 for all {backend.num_qubits} qubits")

# ============================================================================
# STEP 3: TWO-QUBIT GATE ERRORS (from calibration, real coupling pairs)
# ============================================================================
print("\n[3] Pulling two-qubit gate errors...")

coupling_map = backend.configuration().coupling_map
two_qubit_gate_name = backend.configuration().basis_gates
two_qubit_gate = "cz" if "cz" in two_qubit_gate_name else "ecr" if "ecr" in two_qubit_gate_name else "cx"

two_qubit_errors = {}
for pair in coupling_map:
    q0, q1 = pair
    try:
        err = props.gate_error(two_qubit_gate, [q0, q1])
        two_qubit_errors[f"{q0}-{q1}"] = err
    except Exception:
        two_qubit_errors[f"{q0}-{q1}"] = None

print(f"✓ Pulled {two_qubit_gate.upper()} error for all {len(coupling_map)} coupled pairs")

# ============================================================================
# STEP 4: QUBIT TOPOLOGY
# ============================================================================
print("\n[4] Recording qubit connectivity...")

connectivity_data = {
    "total_qubits": backend.num_qubits,
    "total_connections": len(coupling_map),
    "avg_connections_per_qubit": len(coupling_map) * 2 / backend.num_qubits,
    "two_qubit_gate": two_qubit_gate,
    "coupling_map": coupling_map,
}

print(f"✓ Topology recorded ({len(coupling_map)} connections)")

# ============================================================================
# STEP 5: ENTANGLEMENT DECAY (real hardware run)
# ============================================================================
print("\n[5] Running entanglement decay experiment (GHZ state)...")

def create_ghz(n):
    qc = QuantumCircuit(n)
    qc.h(0)
    for i in range(n - 1):
        qc.cx(i, i + 1)
    return qc

N_QUBITS = min(50, backend.num_qubits)
qc = create_ghz(N_QUBITS)

operators = []
for distance in range(1, N_QUBITS - 1):
    op_string = "Z" + "I" * (distance - 1) + "Z" + "I" * (N_QUBITS - distance - 1)
    operators.append(SparsePauliOp(op_string))

print(f"✓ Built {N_QUBITS}-qubit GHZ circuit with {len(operators)} measurement operators")

pm = generate_preset_pass_manager(optimization_level=1, backend=backend)
isa_circuit = pm.run(qc)
isa_operators = [op.apply_layout(isa_circuit.layout) for op in operators]

print(f"✓ Transpiled circuit (depth: {isa_circuit.depth()})")

options = EstimatorOptions()
options.resilience_level = 1
options.dynamical_decoupling.enable = True
options.dynamical_decoupling.sequence_type = "XY4"

estimator = Estimator(backend, options=options)
print("  Submitting job to hardware...")
start = time.time()

job = estimator.run([(isa_circuit, isa_operators)])
job_id = job.job_id()
print(f"✓ Job submitted: {job_id}")
print("  Waiting for result...")

result = job.result()[0]
elapsed = time.time() - start
print(f"✓ Completed in {elapsed:.1f} seconds")

expectation_values = result.data.evs
distances = np.arange(1, len(expectation_values) + 1)
normalized = (expectation_values / expectation_values[0]).tolist()

# ============================================================================
# STEP 6: CIRCUIT DEPTH TEST (real hardware run, not simulated fidelity)
# ============================================================================
print("\n[6] Running circuit depth scaling test...")

depth_circuits = []
depth_operators = []
test_depths = list(range(5, 51, 10))

for depth in test_depths:
    dqc = QuantumCircuit(5)
    for _ in range(depth):
        for i in range(4):
            dqc.cx(i, i + 1)
        for i in range(5):
            dqc.h(i)
    depth_pm = generate_preset_pass_manager(optimization_level=1, backend=backend)
    isa_dqc = depth_pm.run(dqc)
    op = SparsePauliOp("Z" + "I" * 4).apply_layout(isa_dqc.layout)
    depth_circuits.append(isa_dqc)
    depth_operators.append(op)

depth_estimator = Estimator(backend, options=options)
depth_job = depth_estimator.run([(c, [o]) for c, o in zip(depth_circuits, depth_operators)])
depth_job_id = depth_job.job_id()
print(f"✓ Depth test job submitted: {depth_job_id}")

depth_results = depth_job.result()
circuit_depth_tests = [
    {"depth": d, "expectation_value": float(r.data.evs[0])}
    for d, r in zip(test_depths, depth_results)
]
print(f"✓ Depth scaling test complete ({len(test_depths)} depths tested)")

# ============================================================================
# STEP 7: SAVE RAW DATA
# ============================================================================
print("\n[7] Saving raw data...")

data = {
    "metadata": {
        "backend": backend.name,
        "num_qubits": backend.num_qubits,
        "timestamp": datetime.now().isoformat(),
        "entanglement_job_id": job_id,
        "depth_test_job_id": depth_job_id,
    },
    "single_qubit_gate_errors": {str(k): v for k, v in single_qubit_errors.items()},
    "readout_errors": {str(k): v for k, v in readout_errors.items()},
    "t1_times_seconds": {str(k): v for k, v in t1_times.items()},
    "t2_times_seconds": {str(k): v for k, v in t2_times.items()},
    "two_qubit_gate_errors": two_qubit_errors,
    "connectivity": connectivity_data,
    "circuit_depth_tests": circuit_depth_tests,
    "entanglement_data": {
        "num_qubits": N_QUBITS,
        "distances": distances.tolist(),
        "expectation_values": expectation_values.tolist(),
        "normalized_correlations": normalized,
    },
}

with open("quantum_noise_data.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"✓ Raw data saved: quantum_noise_data.json")

print("\n" + "=" * 80)
print("DATA COLLECTION COMPLETE")
print("=" * 80)
print(f"\nEntanglement job ID: {job_id}")
print(f"Depth test job ID: {depth_job_id}")
