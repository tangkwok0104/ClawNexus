use anchor_lang::prelude::*;
use anchor_lang::system_program;

declare_id!("tWrdP9vPV3j4DsJfdyWXdxLEZnRRLJuukkwHdmdipQv");

// ============================================================
// ClawNexus Escrow Program v3 — Trustless On-Chain Mission Payments
//
// Every SOL that flows through ClawNexus is protected by this
// program. No human — not even the founders — can touch funds
// that are locked in escrow. Only the code decides.
//
// v3 Improvements:
//   ✅ Hardcoded treasury validation (no spoofing)
//   ✅ PDA seeds include mentor (tighter isolation)
//   ✅ Escrow account auto-closing (rent reclaimed)
//   ✅ On-chain events (indexable off-chain)
//   ✅ Leaner state (smaller accounts = cheaper)
//   ✅ Full-lamports vault transfer (no dust left behind)
// ============================================================

// ============================================================
// Constants & Security Hardening
// ============================================================

/// Platform commission rate: 2% (represented as basis points)
const PLATFORM_COMMISSION_BPS: u64 = 200; // 200 basis points = 2%

/// Minimum escrow amount in lamports (0.01 SOL = 10_000_000 lamports)
const MIN_ESCROW_LAMPORTS: u64 = 10_000_000;

/// Maximum escrow amount in lamports (100 SOL)
const MAX_ESCROW_LAMPORTS: u64 = 100_000_000_000;

/// Platform treasury public key — hardcoded to prevent spoofing
/// TODO: Replace with actual treasury before mainnet deploy
const PLATFORM_TREASURY: Pubkey = Pubkey::new_from_array([
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
]); // PLACEHOLDER — will be replaced with real treasury bytes

// ============================================================
// Program Instructions
// ============================================================

#[program]
pub mod clawnexus_escrow {
    use super::*;

    /// Create and fund an escrow for a mission.
    ///
    /// The client (Student/Hiring Manager) locks SOL into a PDA vault.
    /// - 2% commission is immediately sent to the platform treasury
    /// - The remaining 98% is held in escrow until mission completion
    pub fn create_escrow(
        ctx: Context<CreateEscrow>,
        mission_id: [u8; 32],
        amount: u64,
        deadline: i64,
    ) -> Result<()> {
        // --- Validation ---
        require!(amount >= MIN_ESCROW_LAMPORTS, EscrowError::AmountTooSmall);
        require!(amount <= MAX_ESCROW_LAMPORTS, EscrowError::AmountTooLarge);

        let clock = Clock::get()?;
        require!(deadline > clock.unix_timestamp, EscrowError::DeadlineInPast);

        // --- Calculate fees using checked math ---
        let commission = amount
            .checked_mul(PLATFORM_COMMISSION_BPS)
            .ok_or(EscrowError::MathOverflow)?
            .checked_div(10_000)
            .ok_or(EscrowError::MathOverflow)?;

        let net_amount = amount
            .checked_sub(commission)
            .ok_or(EscrowError::MathOverflow)?;

        // --- Transfer commission to platform treasury ---
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.client.to_account_info(),
                    to: ctx.accounts.platform_treasury.to_account_info(),
                },
            ),
            commission,
        )?;

        // --- Transfer net amount to escrow vault PDA ---
        system_program::transfer(
            CpiContext::new(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.client.to_account_info(),
                    to: ctx.accounts.escrow_vault.to_account_info(),
                },
            ),
            net_amount,
        )?;

        // --- Initialize escrow account state ---
        let escrow = &mut ctx.accounts.escrow_account;
        escrow.mission_id = mission_id;
        escrow.client = ctx.accounts.client.key();
        escrow.mentor = ctx.accounts.mentor.key();
        escrow.net_amount = net_amount;
        escrow.status = EscrowStatus::Funded;
        escrow.deadline = deadline;
        escrow.bump = ctx.bumps.escrow_account;
        escrow.vault_bump = ctx.bumps.escrow_vault;

        emit!(EscrowCreated {
            mission_id,
            client: escrow.client,
            mentor: escrow.mentor,
            amount: net_amount,
        });

        Ok(())
    }

    /// Release escrow — Client approves the mission, Mentor gets paid.
    ///
    /// ONLY the original client (who funded the escrow) can call this.
    /// The full vault balance goes to the mentor's wallet.
    /// Escrow account is auto-closed (rent reclaimed by client).
    pub fn release_escrow(ctx: Context<ReleaseEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;

        // Only funded escrows can be released
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        let vault_lamports = ctx.accounts.escrow_vault.lamports();

        let seeds = &[
            b"vault",
            escrow.mission_id.as_ref(),
            escrow.client.as_ref(),
            escrow.mentor.as_ref(),
            &[escrow.vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.mentor.to_account_info(),
                },
                &[seeds],
            ),
            vault_lamports,
        )?;

        emit!(EscrowReleased {
            mission_id: escrow.mission_id,
            mentor: escrow.mentor,
            amount: vault_lamports,
        });

        Ok(())
    }

    /// Refund escrow — Client cancels the mission, gets net amount back.
    ///
    /// Platform keeps the 2% commission (non-refundable processing fee).
    /// ONLY the original client can call this.
    /// Escrow account is auto-closed (rent reclaimed by client).
    pub fn refund_escrow(ctx: Context<RefundEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;

        // Only funded escrows can be refunded
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        let vault_lamports = ctx.accounts.escrow_vault.lamports();

        let seeds = &[
            b"vault",
            escrow.mission_id.as_ref(),
            escrow.client.as_ref(),
            escrow.mentor.as_ref(),
            &[escrow.vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.client.to_account_info(),
                },
                &[seeds],
            ),
            vault_lamports,
        )?;

        emit!(EscrowRefunded {
            mission_id: escrow.mission_id,
            client: escrow.client,
            amount: vault_lamports,
        });

        Ok(())
    }

    /// Expire escrow — Anyone can call this after the deadline passes.
    ///
    /// Acts as a permissionless crank: if the deadline has passed and the
    /// escrow is still in Funded status, auto-refund to the client.
    /// Escrow account is auto-closed (rent reclaimed by client).
    pub fn expire_escrow(ctx: Context<ExpireEscrow>) -> Result<()> {
        let escrow = &ctx.accounts.escrow_account;
        let clock = Clock::get()?;

        // Only funded escrows can expire
        require!(
            escrow.status == EscrowStatus::Funded,
            EscrowError::InvalidStatus
        );

        // Check deadline
        require!(
            clock.unix_timestamp >= escrow.deadline,
            EscrowError::DeadlineNotReached
        );

        let vault_lamports = ctx.accounts.escrow_vault.lamports();

        let seeds = &[
            b"vault",
            escrow.mission_id.as_ref(),
            escrow.client.as_ref(),
            escrow.mentor.as_ref(),
            &[escrow.vault_bump],
        ];

        system_program::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.system_program.to_account_info(),
                system_program::Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.client.to_account_info(),
                },
                &[seeds],
            ),
            vault_lamports,
        )?;

        emit!(EscrowExpired {
            mission_id: escrow.mission_id,
            client: escrow.client,
            amount: vault_lamports,
        });

        Ok(())
    }
}

// ============================================================
// Instruction Account Contexts (Optimized Ordering)
// ============================================================

#[derive(Accounts)]
#[instruction(mission_id: [u8; 32])]
pub struct CreateEscrow<'info> {
    /// The client funding the escrow (must sign)
    #[account(mut)]
    pub client: Signer<'info>,

    /// The mentor who will receive payment (not a signer — just a reference)
    /// CHECK: We only store the public key; no data is read from this account
    pub mentor: UncheckedAccount<'info>,

    /// The escrow state account (PDA) — seeds include mentor for tighter isolation
    #[account(
        init,
        payer = client,
        space = 8 + EscrowAccount::INIT_SPACE,
        seeds = [b"escrow", mission_id.as_ref(), client.key().as_ref(), mentor.key().as_ref()],
        bump,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault (PDA) that holds the SOL
    #[account(
        mut,
        seeds = [b"vault", mission_id.as_ref(), client.key().as_ref(), mentor.key().as_ref()],
        bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    /// Platform treasury — hardcoded validation prevents spoofing
    /// CHECK: Validated against PLATFORM_TREASURY constant
    #[account(
        mut,
        constraint = platform_treasury.key() == PLATFORM_TREASURY
    )]
    pub platform_treasury: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ReleaseEscrow<'info> {
    /// The client who originally funded the escrow (must sign to approve)
    #[account(mut)]
    pub client: Signer<'info>,

    /// Mentor declared BEFORE escrow_account so seeds/constraint can reference it
    #[account(mut)]
    pub mentor: SystemAccount<'info>,

    /// Escrow PDA — auto-closes to client (rent reclaimed)
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), client.key().as_ref(), mentor.key().as_ref()],
        bump = escrow_account.bump,
        close = client,
        constraint = mentor.key() == escrow_account.mentor @ EscrowError::Unauthorized,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault — all lamports transferred to mentor
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), client.key().as_ref(), mentor.key().as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RefundEscrow<'info> {
    /// The client requesting the refund (must be the original funder)
    #[account(mut)]
    pub client: Signer<'info>,

    /// The escrow state account — auto-closes to client (rent reclaimed)
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), client.key().as_ref(), escrow_account.mentor.as_ref()],
        bump = escrow_account.bump,
        close = client,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The escrow vault — all lamports transferred back to client
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), client.key().as_ref(), escrow_account.mentor.as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct ExpireEscrow<'info> {
    /// Anyone can call expire (permissionless crank)
    pub caller: Signer<'info>,

    /// The escrow state account — auto-closes to client
    #[account(
        mut,
        seeds = [b"escrow", escrow_account.mission_id.as_ref(), escrow_account.client.as_ref(), escrow_account.mentor.as_ref()],
        bump = escrow_account.bump,
        close = client,
    )]
    pub escrow_account: Account<'info, EscrowAccount>,

    /// The client who originally funded (receives the refund + rent)
    #[account(
        mut,
        constraint = client.key() == escrow_account.client @ EscrowError::Unauthorized,
    )]
    pub client: SystemAccount<'info>,

    /// The escrow vault — all lamports transferred back to client
    #[account(
        mut,
        seeds = [b"vault", escrow_account.mission_id.as_ref(), client.key().as_ref(), escrow_account.mentor.as_ref()],
        bump = escrow_account.vault_bump,
    )]
    pub escrow_vault: SystemAccount<'info>,

    pub system_program: Program<'info, System>,
}

// ============================================================
// On-Chain State
// ============================================================

#[account]
#[derive(InitSpace)]
pub struct EscrowAccount {
    /// Unique mission identifier (32 bytes)
    pub mission_id: [u8; 32],
    /// Public key of the client who funded the escrow
    pub client: Pubkey,
    /// Public key of the mentor who will receive payment
    pub mentor: Pubkey,
    /// Amount the mentor receives (gross - commission, in lamports)
    pub net_amount: u64,
    /// Current escrow status
    pub status: EscrowStatus,
    /// Auto-refund deadline (unix timestamp)
    pub deadline: i64,
    /// PDA bump for the escrow account
    pub bump: u8,
    /// PDA bump for the vault account
    pub vault_bump: u8,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Copy, PartialEq, Eq, InitSpace)]
pub enum EscrowStatus {
    Funded,
    Completed,
    Refunded,
    Expired,
}

// ============================================================
// Events (indexable off-chain)
// ============================================================

#[event]
pub struct EscrowCreated {
    pub mission_id: [u8; 32],
    pub client: Pubkey,
    pub mentor: Pubkey,
    pub amount: u64,
}

#[event]
pub struct EscrowReleased {
    pub mission_id: [u8; 32],
    pub mentor: Pubkey,
    pub amount: u64,
}

#[event]
pub struct EscrowRefunded {
    pub mission_id: [u8; 32],
    pub client: Pubkey,
    pub amount: u64,
}

#[event]
pub struct EscrowExpired {
    pub mission_id: [u8; 32],
    pub client: Pubkey,
    pub amount: u64,
}

// ============================================================
// Custom Errors
// ============================================================

#[error_code]
pub enum EscrowError {
    #[msg("Amount too small")]
    AmountTooSmall,
    #[msg("Amount too large")]
    AmountTooLarge,
    #[msg("Deadline in past")]
    DeadlineInPast,
    #[msg("Deadline not reached")]
    DeadlineNotReached,
    #[msg("Invalid status")]
    InvalidStatus,
    #[msg("Unauthorized access")]
    Unauthorized,
    #[msg("Math overflow")]
    MathOverflow,
}
