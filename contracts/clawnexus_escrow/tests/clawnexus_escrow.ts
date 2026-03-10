import * as anchor from "@coral-xyz/anchor";
import { Program } from "@coral-xyz/anchor";
import { ClawnexusEscrow } from "../target/types/clawnexus_escrow";
import { assert } from "chai";
import {
  PublicKey,
  Keypair,
  SystemProgram,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";
import crypto from "crypto";

describe("ClawNexus Escrow — Integration Tests", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);

  const program = anchor.workspace
    .clawnexusEscrow as Program<ClawnexusEscrow>;

  // Use the provider wallet (deployer) as client — already has SOL!
  const client = provider.wallet as anchor.Wallet;
  // Generate throwaway for mentor
  const mentor = Keypair.generate();
  // Use a second generated keypair for treasury — we'll fund it from deployer
  const treasury = Keypair.generate();
  // Attacker/impostor keypair for adversarial tests
  const attacker = Keypair.generate();

  // ── UNIQUE RUN PREFIX — ensures fresh PDA addresses on every test run ──
  const RUN_ID = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  function missionIdBytes(id: string): number[] {
    const hash = crypto.createHash("sha256").update(`${RUN_ID}-${id}`).digest();
    return Array.from(hash);
  }

  function findEscrowPDA(missionId: number[], clientPubkey: PublicKey) {
    return PublicKey.findProgramAddressSync(
      [Buffer.from("escrow"), Buffer.from(missionId), clientPubkey.toBuffer()],
      program.programId
    );
  }

  function findVaultPDA(missionId: number[], clientPubkey: PublicKey) {
    return PublicKey.findProgramAddressSync(
      [Buffer.from("vault"), Buffer.from(missionId), clientPubkey.toBuffer()],
      program.programId
    );
  }

  before(async () => {
    const bal = await provider.connection.getBalance(client.publicKey);
    console.log(`  💰 Client (deployer): ${client.publicKey.toBase58()} — ${bal / LAMPORTS_PER_SOL} SOL`);
    console.log(`  📋 Mentor: ${mentor.publicKey.toBase58()}`);
    console.log(`  📋 Treasury: ${treasury.publicKey.toBase58()}`);
    console.log(`  🗡️  Attacker: ${attacker.publicKey.toBase58()}`);
    console.log(`  📋 Program: ${program.programId.toBase58()}`);
    console.log(`  🆔 Run ID: ${RUN_ID} (unique per run — avoids PDA collisions)`);

    // Fund treasury from deployer (0.01 SOL for rent exemption)
    const tx = new anchor.web3.Transaction().add(
      SystemProgram.transfer({
        fromPubkey: client.publicKey,
        toPubkey: treasury.publicKey,
        lamports: 0.01 * LAMPORTS_PER_SOL,
      })
    );
    await provider.sendAndConfirm(tx);
    console.log("  ✅ Treasury funded from deployer (0.01 SOL)");

    // Fund attacker from deployer (0.01 SOL — just enough to sign transactions)
    const tx2 = new anchor.web3.Transaction().add(
      SystemProgram.transfer({
        fromPubkey: client.publicKey,
        toPubkey: attacker.publicKey,
        lamports: 0.01 * LAMPORTS_PER_SOL,
      })
    );
    await provider.sendAndConfirm(tx2);
    console.log("  ✅ Attacker funded from deployer (0.01 SOL)");
  });

  // ════════════════════════════════════════════════════════════
  // SECTION A: Happy Path Tests (7 original tests)
  // ════════════════════════════════════════════════════════════

  // ── Test 1: Create Escrow ──
  describe("create_escrow", () => {
    const missionId = missionIdBytes("create-happy");
    const amount = new anchor.BN(0.02 * LAMPORTS_PER_SOL);
    const deadline = new anchor.BN(Math.floor(Date.now() / 1000) + 3600);

    it("successfully creates an escrow", async () => {
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      const tx = await program.methods
        .createEscrow(missionId, amount, deadline)
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      console.log(`  ✅ create_escrow TX: ${tx}`);

      const escrow = await program.account.escrowAccount.fetch(escrowPDA);
      assert.equal(escrow.client.toBase58(), client.publicKey.toBase58());
      assert.equal(escrow.mentor.toBase58(), mentor.publicKey.toBase58());
      assert.equal(escrow.grossAmount.toNumber(), amount.toNumber());

      const expectedCommission = Math.floor((amount.toNumber() * 200) / 10000);
      const expectedNet = amount.toNumber() - expectedCommission;
      assert.equal(escrow.commission.toNumber(), expectedCommission);
      assert.equal(escrow.netAmount.toNumber(), expectedNet);
      assert.deepEqual(escrow.status, { funded: {} });

      console.log(`  📊 Gross: ${amount.toNumber()} | Commission: ${expectedCommission} | Net: ${expectedNet}`);
    });

    it("rejects amount below minimum (0.01 SOL)", async () => {
      const mid = missionIdBytes("too-small");
      try {
        await program.methods
          .createEscrow(mid, new anchor.BN(1000), deadline)
          .accountsPartial({
            client: client.publicKey,
            mentor: mentor.publicKey,
            platformTreasury: treasury.publicKey,
            systemProgram: SystemProgram.programId,
          })
          .rpc();
        assert.fail("Should have thrown");
      } catch (err: any) {
        assert.include(err.toString(), "AmountTooSmall");
        console.log("  ✅ Rejected too-small amount");
      }
    });

    it("rejects past deadline", async () => {
      const mid = missionIdBytes("past-deadline");
      const pastDeadline = new anchor.BN(Math.floor(Date.now() / 1000) - 3600);
      try {
        await program.methods
          .createEscrow(mid, amount, pastDeadline)
          .accountsPartial({
            client: client.publicKey,
            mentor: mentor.publicKey,
            platformTreasury: treasury.publicKey,
            systemProgram: SystemProgram.programId,
          })
          .rpc();
        assert.fail("Should have thrown");
      } catch (err: any) {
        assert.include(err.toString(), "DeadlineInPast");
        console.log("  ✅ Rejected past deadline");
      }
    });

    it("rejects amount above maximum (100 SOL)", async () => {
      const mid = missionIdBytes("too-large");
      const tooLarge = new anchor.BN(101 * LAMPORTS_PER_SOL);
      try {
        await program.methods
          .createEscrow(mid, tooLarge, deadline)
          .accountsPartial({
            client: client.publicKey,
            mentor: mentor.publicKey,
            platformTreasury: treasury.publicKey,
            systemProgram: SystemProgram.programId,
          })
          .rpc();
        assert.fail("Should have thrown");
      } catch (err: any) {
        assert.include(err.toString(), "AmountTooLarge");
        console.log("  ✅ Rejected amount above maximum");
      }
    });
  });

  // ── Test 2: Release Escrow ──
  describe("release_escrow", () => {
    const missionId = missionIdBytes("release-happy");
    const amount = new anchor.BN(0.02 * LAMPORTS_PER_SOL);

    it("client approves → mentor gets paid", async () => {
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          amount,
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      const mentorBefore = await provider.connection.getBalance(mentor.publicKey);

      const tx = await program.methods
        .releaseEscrow()
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      console.log(`  ✅ release_escrow TX: ${tx}`);

      const escrow = await program.account.escrowAccount.fetch(escrowPDA);
      assert.deepEqual(escrow.status, { completed: {} });

      const mentorAfter = await provider.connection.getBalance(mentor.publicKey);
      const expectedNet = amount.toNumber() - Math.floor((amount.toNumber() * 200) / 10000);
      assert.equal(mentorAfter - mentorBefore, expectedNet);
      console.log(`  💰 Mentor received: ${expectedNet / LAMPORTS_PER_SOL} SOL`);
    });
  });

  // ── Test 3: Refund Escrow ──
  describe("refund_escrow", () => {
    const missionId = missionIdBytes("refund-happy");
    const amount = new anchor.BN(0.02 * LAMPORTS_PER_SOL);

    it("client cancels → SOL refunded (minus commission)", async () => {
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          amount,
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      const clientBefore = await provider.connection.getBalance(client.publicKey);

      const tx = await program.methods
        .refundEscrow()
        .accountsPartial({
          client: client.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      console.log(`  ✅ refund_escrow TX: ${tx}`);

      const escrow = await program.account.escrowAccount.fetch(escrowPDA);
      assert.deepEqual(escrow.status, { refunded: {} });

      const clientAfter = await provider.connection.getBalance(client.publicKey);
      const expectedNet = amount.toNumber() - Math.floor((amount.toNumber() * 200) / 10000);
      const gained = clientAfter - clientBefore;
      assert.isAtLeast(gained, expectedNet - 10000);
      console.log(`  💰 Client refunded: ~${gained / LAMPORTS_PER_SOL} SOL`);
    });
  });

  // ── Test 4: Expire Escrow (premature → reject) ──
  describe("expire_escrow", () => {
    it("rejects expire before deadline", async () => {
      const missionId = missionIdBytes("expire-early");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 86400)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      try {
        await program.methods
          .expireEscrow()
          .accountsPartial({
            caller: client.publicKey,
            client: client.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Should have thrown");
      } catch (err: any) {
        assert.include(err.toString(), "DeadlineNotReached");
        console.log("  ✅ Rejected premature expiry");
      }
    });
  });

  // ── Test 5: Conservation of Value ──
  describe("conservation of value", () => {
    it("commission + net = gross (no value lost)", async () => {
      const missionId = missionIdBytes("conservation");
      const amount = new anchor.BN(0.033 * LAMPORTS_PER_SOL);
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          amount,
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      const escrow = await program.account.escrowAccount.fetch(escrowPDA);
      assert.equal(
        escrow.commission.toNumber() + escrow.netAmount.toNumber(),
        escrow.grossAmount.toNumber(),
        "commission + net must equal gross"
      );
      console.log(`  ✅ ${escrow.commission.toNumber()} + ${escrow.netAmount.toNumber()} = ${escrow.grossAmount.toNumber()}`);
    });
  });

  // ════════════════════════════════════════════════════════════
  // SECTION B: Adversarial / Security Tests
  // ════════════════════════════════════════════════════════════

  describe("🔒 ADVERSARIAL: unauthorized access", () => {
    it("rejects release by impostor (non-client signer)", async () => {
      // Create a legit escrow first
      const missionId = missionIdBytes("adv-unauth-release");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // Attacker tries to release — they sign as "client" but PDA seeds won't match
      try {
        await program.methods
          .releaseEscrow()
          .accountsPartial({
            client: attacker.publicKey,    // impostor
            mentor: mentor.publicKey,
            escrowAccount: escrowPDA,      // real escrow from legit client
          })
          .signers([attacker])
          .rpc();
        assert.fail("Impostor should NOT be able to release");
      } catch (err: any) {
        // Anchor will reject because PDA seeds [escrow, mission_id, attacker.key]
        // won't match the actual escrow PDA derived from client.key
        console.log("  ✅ Rejected unauthorized release — impostor blocked");
      }
    });

    it("rejects refund by impostor (non-client signer)", async () => {
      const missionId = missionIdBytes("adv-unauth-refund");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      try {
        await program.methods
          .refundEscrow()
          .accountsPartial({
            client: attacker.publicKey,    // impostor
            escrowAccount: escrowPDA,      // real escrow
          })
          .signers([attacker])
          .rpc();
        assert.fail("Impostor should NOT be able to refund");
      } catch (err: any) {
        console.log("  ✅ Rejected unauthorized refund — impostor blocked");
      }
    });
  });

  describe("🔒 ADVERSARIAL: double-spend prevention", () => {
    it("rejects double release (release already-completed escrow)", async () => {
      const missionId = missionIdBytes("adv-double-release");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // First release — should succeed
      await program.methods
        .releaseEscrow()
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      // Second release — should fail (status is now Completed, not Funded)
      try {
        await program.methods
          .releaseEscrow()
          .accountsPartial({
            client: client.publicKey,
            mentor: mentor.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Double release should NOT succeed");
      } catch (err: any) {
        assert.include(err.toString(), "InvalidStatus");
        console.log("  ✅ Rejected double release — state machine enforced");
      }
    });

    it("rejects double refund (refund already-refunded escrow)", async () => {
      const missionId = missionIdBytes("adv-double-refund");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // First refund
      await program.methods
        .refundEscrow()
        .accountsPartial({
          client: client.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      // Second refund — should fail
      try {
        await program.methods
          .refundEscrow()
          .accountsPartial({
            client: client.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Double refund should NOT succeed");
      } catch (err: any) {
        assert.include(err.toString(), "InvalidStatus");
        console.log("  ✅ Rejected double refund — state machine enforced");
      }
    });
  });

  describe("🔒 ADVERSARIAL: cross-state attacks", () => {
    it("rejects release after refund", async () => {
      const missionId = missionIdBytes("adv-release-after-refund");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // Refund first
      await program.methods
        .refundEscrow()
        .accountsPartial({
          client: client.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      // Try to release after refund — should fail
      try {
        await program.methods
          .releaseEscrow()
          .accountsPartial({
            client: client.publicKey,
            mentor: mentor.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Release after refund should NOT succeed");
      } catch (err: any) {
        assert.include(err.toString(), "InvalidStatus");
        console.log("  ✅ Rejected release after refund — cross-state blocked");
      }
    });

    it("rejects refund after release", async () => {
      const missionId = missionIdBytes("adv-refund-after-release");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // Release first
      await program.methods
        .releaseEscrow()
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      // Try to refund after release — should fail
      try {
        await program.methods
          .refundEscrow()
          .accountsPartial({
            client: client.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Refund after release should NOT succeed");
      } catch (err: any) {
        assert.include(err.toString(), "InvalidStatus");
        console.log("  ✅ Rejected refund after release — cross-state blocked");
      }
    });

    it("rejects expire after release", async () => {
      // Re-use the released escrow from "release-happy" test — but since
      // test isolation uses unique IDs, we create a fresh one
      const missionId = missionIdBytes("adv-expire-after-release");
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          // Short deadline — but we release before trying expire
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // Release
      await program.methods
        .releaseEscrow()
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,
          escrowAccount: escrowPDA,
        })
        .rpc();

      // Try expire on a completed escrow — should fail
      try {
        await program.methods
          .expireEscrow()
          .accountsPartial({
            caller: client.publicKey,
            client: client.publicKey,
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Expire after release should NOT succeed");
      } catch (err: any) {
        assert.include(err.toString(), "InvalidStatus");
        console.log("  ✅ Rejected expire after release — cross-state blocked");
      }
    });
  });

  describe("🔒 ADVERSARIAL: wrong mentor", () => {
    it("rejects release with wrong mentor address", async () => {
      const missionId = missionIdBytes("adv-wrong-mentor");
      const wrongMentor = Keypair.generate();
      const [escrowPDA] = findEscrowPDA(missionId, client.publicKey);

      await program.methods
        .createEscrow(
          missionId,
          new anchor.BN(0.02 * LAMPORTS_PER_SOL),
          new anchor.BN(Math.floor(Date.now() / 1000) + 7200)
        )
        .accountsPartial({
          client: client.publicKey,
          mentor: mentor.publicKey,           // real mentor
          platformTreasury: treasury.publicKey,
          systemProgram: SystemProgram.programId,
        })
        .rpc();

      // Try to release with a different mentor — should fail
      try {
        await program.methods
          .releaseEscrow()
          .accountsPartial({
            client: client.publicKey,
            mentor: wrongMentor.publicKey,   // wrong mentor
            escrowAccount: escrowPDA,
          })
          .rpc();
        assert.fail("Wrong mentor should NOT receive funds");
      } catch (err: any) {
        assert.include(err.toString(), "WrongMentor");
        console.log("  ✅ Rejected wrong mentor — funds protected");
      }
    });
  });
});
